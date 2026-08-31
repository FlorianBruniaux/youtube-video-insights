"""Bounded, no-write topic discovery through local yt-dlp metadata search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Callable, Protocol

from yt_insights.downloader import VideoInfo, VideoListResult, fetch_video_list

from .models import CandidateStatus, QuerySpec, ResearchCandidate, normalize_research_text


_MAX_QUERIES = 8
_MAX_CANDIDATES = 10
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    provider_name: str
    provider_version: int
    candidates: tuple[ResearchCandidate, ...]
    errors: tuple[str, ...]
    completed: bool


class DiscoveryProvider(Protocol):
    def discover(
        self, queries: tuple[QuerySpec, ...], *, limit: int = 10
    ) -> DiscoveryResult: ...


@dataclass(slots=True)
class _CandidateMetadata:
    video: VideoInfo
    original_rank: int
    matched_queries: list[str]


class YtDlpDiscoveryProvider:
    """Preview up to ten metadata candidates without changing local data."""

    name = "yt-dlp"
    version = 1

    def __init__(
        self,
        *,
        fetcher: Callable[[str], VideoListResult] = fetch_video_list,
        existing_ids: Callable[[tuple[str, ...]], frozenset[str]],
    ) -> None:
        self._fetcher = fetcher
        self._existing_ids = existing_ids

    def discover(
        self, queries: tuple[QuerySpec, ...], *, limit: int = 10
    ) -> DiscoveryResult:
        self._validate_request(queries, limit)
        errors: list[str] = []
        candidates_by_id: dict[str, _CandidateMetadata] = {}

        for query in queries:
            source = f"ytsearch10:{query.text}"
            try:
                result = self._fetcher(source)
            except Exception:
                self._append_error(errors, "provider_exit_nonzero")
                continue
            if not isinstance(result, VideoListResult):
                self._append_error(errors, "invalid_metadata_record")
                continue
            if result.returncode != 0:
                self._append_error(errors, "provider_exit_nonzero")
            if result.errors:
                self._append_error(errors, "partial_metadata")
            for rank, video in enumerate(result.videos[:_MAX_CANDIDATES], start=1):
                if not self._valid_video(video):
                    self._append_error(errors, "invalid_metadata_record")
                    continue
                metadata = candidates_by_id.get(video.video_id)
                if metadata is None:
                    candidates_by_id[video.video_id] = _CandidateMetadata(
                        video=video,
                        original_rank=rank,
                        matched_queries=[query.text],
                    )
                elif query.text not in metadata.matched_queries:
                    metadata.matched_queries.append(query.text)

        try:
            known_ids = self._existing_ids(tuple(candidates_by_id))
        except Exception:
            self._append_error(errors, "catalog_membership_failed")
            return DiscoveryResult(
                provider_name=self.name,
                provider_version=self.version,
                candidates=(),
                errors=tuple(errors),
                completed=False,
            )

        unseen = [
            metadata
            for video_id, metadata in candidates_by_id.items()
            if video_id not in known_ids
        ]
        selected = self._round_robin_channels(unseen, limit)
        result_candidates = tuple(
            self._to_research_candidate(metadata) for metadata in selected
        )
        if not result_candidates:
            self._append_error(errors, "no_candidates")
        return DiscoveryResult(
            provider_name=self.name,
            provider_version=self.version,
            candidates=result_candidates,
            errors=tuple(errors),
            completed=bool(result_candidates) and not errors,
        )

    @staticmethod
    def _validate_request(queries: tuple[QuerySpec, ...], limit: int) -> None:
        if not isinstance(queries, tuple) or not 1 <= len(queries) <= _MAX_QUERIES:
            raise ValueError("queries must contain between 1 and 8 items")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_CANDIDATES
        ):
            raise ValueError("limit must be between 1 and 10")
        normalized_queries: set[str] = set()
        for query in queries:
            if not isinstance(query, QuerySpec):
                raise TypeError("queries must contain QuerySpec values")
            normalized = normalize_research_text(query.text)
            if normalized.startswith("ytsearch"):
                raise ValueError("query must not start with ytsearch")
            if normalized in normalized_queries:
                raise ValueError("queries must not contain normalized duplicates")
            normalized_queries.add(normalized)

    @staticmethod
    def _valid_video(video: object) -> bool:
        if not isinstance(video, VideoInfo):
            return False
        if _VIDEO_ID.fullmatch(video.video_id) is None:
            return False
        return (
            isinstance(video.title, str)
            and "\x00" not in video.title
            and len(video.title) <= 1_000
        )

    @staticmethod
    def _append_error(errors: list[str], code: str) -> None:
        if code not in errors:
            errors.append(code)

    @staticmethod
    def _round_robin_channels(
        candidates: list[_CandidateMetadata], limit: int
    ) -> list[_CandidateMetadata]:
        by_channel: dict[str, list[_CandidateMetadata]] = {}
        for metadata in candidates:
            channel_id = YtDlpDiscoveryProvider._bounded_channel_value(
                metadata.video.channel_id
            )
            key = (
                channel_id
                if channel_id is not None
                else f"video:{metadata.video.video_id}"
            )
            by_channel.setdefault(key, []).append(metadata)
        channel_groups = sorted(
            by_channel.values(), key=lambda group: group[0].original_rank
        )
        selected: list[_CandidateMetadata] = []
        offset = 0
        while len(selected) < limit:
            added = False
            for channel_candidates in channel_groups:
                if offset < len(channel_candidates):
                    selected.append(channel_candidates[offset])
                    added = True
                    if len(selected) == limit:
                        return selected
            if not added:
                return selected
            offset += 1
        return selected

    @classmethod
    def _to_research_candidate(cls, metadata: _CandidateMetadata) -> ResearchCandidate:
        video = metadata.video
        return ResearchCandidate(
            video_id=video.video_id,
            title=video.title,
            channel_id=cls._bounded_channel_value(video.channel_id),
            channel_title=cls._bounded_channel_value(video.channel_title),
            published_at=cls._published_at(video.upload_date),
            watch_url=f"https://www.youtube.com/watch?v={video.video_id}",
            matched_queries=tuple(metadata.matched_queries),
            original_rank=metadata.original_rank,
            status=CandidateStatus.CANDIDATE,
        )

    @staticmethod
    def _bounded_channel_value(value: object) -> str | None:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 300
            or "\x00" in value
        ):
            return None
        return value

    @staticmethod
    def _published_at(value: object) -> date | None:
        if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
            return None
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError:
            return None
