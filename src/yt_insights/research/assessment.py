"""Read-only, bounded evidence collection for local research assessment.

An assessment spans independent immutable SQLite databases.  It is therefore
not a cross-database transaction snapshot: the identities captured before
querying are checked again after the final query, and a changed database makes
the caller retry rather than receive mixed-generation evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from yt_insights.catalog import Catalog, CatalogError
from yt_insights.search.models import SearchQuery
from yt_insights.search.service import SearchService
from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex

from .models import (
    CoverageMetrics,
    DatabaseSnapshot,
    FreshnessAssessment,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    ResearchAssessment,
    VideoEvidence,
)


_ASSESSMENT_LIMIT = 20
_YOUTUBE_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")


class AssessmentRetryableError(RuntimeError):
    """Raised when bounded local evidence cannot be assessed consistently."""


class EvidenceReader(Protocol):
    """The read-only boundary used by local assessment aggregation."""

    def capture_snapshot(self) -> DatabaseSnapshot: ...

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None: ...

    def search_passages(
        self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
    ) -> tuple[PassageEvidence, ...]: ...

    def search_videos(
        self, query: QuerySpec, *, limit: int
    ) -> tuple[VideoEvidence, ...]: ...


class SQLiteEvidenceReader:
    """Map validated local-search and catalogue records to evidence contracts."""

    def __init__(self, *, search_database: Path, catalog_database: Path) -> None:
        self._search_database = Path(search_database)
        self._catalog_database = Path(catalog_database)

    def capture_snapshot(self) -> DatabaseSnapshot:
        """Capture opaque local-generation values without retaining file paths."""
        try:
            return DatabaseSnapshot(
                search_generation=_database_generation(self._search_database),
                catalog_generation=_database_generation(self._catalog_database),
            )
        except AssessmentRetryableError:
            raise
        except OSError as exc:
            raise AssessmentRetryableError("local evidence database is unavailable") from None

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None:
        """Reject any published replacement observed during an assessment."""
        try:
            current = self.capture_snapshot()
        except AssessmentRetryableError:
            raise AssessmentRetryableError("local evidence changed during assessment") from None
        if current != snapshot:
            raise AssessmentRetryableError("local evidence changed during assessment")

    def search_passages(
        self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
    ) -> tuple[PassageEvidence, ...]:
        """Return at most ``limit`` timestamped hits, with deterministic filters."""
        _require_bounded_limit(limit)
        _require_languages(languages)
        try:
            language_values = languages if languages else (None,)
            candidates: list[tuple[int, PassageEvidence]] = []
            for language_position, language in enumerate(language_values):
                hits = SearchService(SQLiteFtsIndex(self._search_database)).search(
                    SearchQuery(query.text, language=language, limit=limit)
                )
                candidates.extend(
                    (
                        language_position,
                        PassageEvidence(
                            query=query.text,
                            passage_id=hit.passage.passage_id,
                            video_id=hit.document.video_id,
                            channel_id=hit.document.channel_id,
                            rank=hit.rank,
                            url=hit.passage.youtube_url,
                            excerpt=hit.excerpt or hit.passage.text,
                            source_sha256=hit.document.source_sha256,
                        ),
                    )
                    for hit in hits
                )
            by_passage: dict[str, tuple[int, PassageEvidence]] = {}
            for language_position, evidence in candidates:
                previous = by_passage.get(evidence.passage_id)
                if previous is None or (language_position, evidence.rank) < (
                    previous[0],
                    previous[1].rank,
                ):
                    by_passage[evidence.passage_id] = (language_position, evidence)
            return tuple(
                item[1]
                for item in sorted(
                    by_passage.values(),
                    key=lambda item: (item[0], item[1].rank, item[1].passage_id),
                )[:limit]
            )
        except (SearchIndexError, OSError, ValueError, TypeError) as exc:
            raise AssessmentRetryableError("local search evidence is unavailable") from None

    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]:
        """Return bounded catalogue metadata, retaining source slugs as provenance."""
        _require_bounded_limit(limit)
        try:
            with Catalog.open_read_only(self._catalog_database) as catalog:
                results = catalog.search_videos(query.text, limit=limit)
            return tuple(
                VideoEvidence(
                    query=query.text,
                    video_id=result.video_id,
                    source_keys=result.sources,
                    title=result.title,
                    published_at=result.published_at,
                    rank=result.rank,
                    watch_url=result.watch_url,
                )
                for result in results
            )
        except (CatalogError, OSError, ValueError, TypeError) as exc:
            raise AssessmentRetryableError("local catalogue evidence is unavailable") from None


def assess_local(
    *,
    queries: tuple[QuerySpec, ...],
    profile: FreshnessProfile,
    evidence_reader: EvidenceReader,
    last_successful_discovery_at: datetime | None,
    now: datetime,
    languages: tuple[str, ...] = (),
) -> ResearchAssessment:
    """Aggregate bounded local evidence and deterministic discovery freshness."""
    _require_queries(queries)
    _require_languages(languages)
    _require_utc(now, label="now")
    if last_successful_discovery_at is not None:
        _require_utc(last_successful_discovery_at, label="last successful discovery")
    if not isinstance(profile, FreshnessProfile):
        raise TypeError("profile must be a FreshnessProfile")

    snapshot = evidence_reader.capture_snapshot()
    selected_passages: dict[str, tuple[int, PassageEvidence]] = {}
    selected_videos: dict[str, tuple[int, VideoEvidence]] = {}
    zero_hit_queries: list[str] = []
    for query_position, query in enumerate(queries):
        passages = evidence_reader.search_passages(
            query, languages=languages, limit=_ASSESSMENT_LIMIT
        )
        videos = evidence_reader.search_videos(query, limit=_ASSESSMENT_LIMIT)
        if not passages and not videos:
            zero_hit_queries.append(query.text)
        _select_best(selected_passages, passages, query_position, identity="passage_id")
        _select_best(selected_videos, videos, query_position, identity="video_id")
    evidence_reader.validate_snapshot(snapshot)

    passages = _ordered_unique(selected_passages, identity="passage_id")
    videos = _ordered_unique(selected_videos, identity="video_id")
    coverage = CoverageMetrics(
        matched_passages=len(passages),
        matched_videos=len({item.video_id for item in passages} | {item.video_id for item in videos}),
        distinct_channels=len(
            {
                item.channel_id
                for item in passages
                if _YOUTUBE_CHANNEL_ID.fullmatch(item.channel_id) is not None
            }
        ),
        queries_with_zero_hits=tuple(zero_hit_queries),
        newest_source_published_at=_newest_publication_date(videos),
        unknown_publication_date_count=sum(item.published_at is None for item in videos),
    )
    return ResearchAssessment(
        created_at=now,
        snapshot=snapshot,
        coverage=coverage,
        freshness=_freshness(profile, last_successful_discovery_at, now),
        passages=passages,
        videos=videos,
    )


def _database_generation(path: Path) -> str:
    """Hash only stable file identity fields obtained from an opened regular file."""
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        named = os.lstat(path)
    except FileNotFoundError as exc:
        raise AssessmentRetryableError("local evidence database is unavailable") from None
    except OSError as exc:
        raise AssessmentRetryableError("local evidence database is unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode):
        raise AssessmentRetryableError("local evidence database is unavailable")
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise AssessmentRetryableError("local evidence changed during assessment")
    payload = json.dumps(
        {
            "device": opened.st_dev,
            "inode": opened.st_ino,
            "mtime_ns": opened.st_mtime_ns,
            "size": opened.st_size,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_bounded_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _ASSESSMENT_LIMIT:
        raise ValueError("evidence limit must be between 1 and 20")


def _require_languages(languages: tuple[str, ...]) -> None:
    if not isinstance(languages, tuple) or not all(isinstance(language, str) and language for language in languages):
        raise TypeError("languages must be a tuple of non-empty strings")


def _require_queries(queries: tuple[QuerySpec, ...]) -> None:
    if not isinstance(queries, tuple) or not queries or not all(isinstance(query, QuerySpec) for query in queries):
        raise ValueError("queries must contain QuerySpec values")


def _require_utc(value: datetime, *, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is not UTC:
        raise ValueError(f"{label} must be timezone-aware UTC")


def _select_best(
    selected: dict[str, tuple[int, PassageEvidence | VideoEvidence]],
    candidates: tuple[PassageEvidence, ...] | tuple[VideoEvidence, ...],
    query_position: int,
    *,
    identity: str,
) -> None:
    for candidate in candidates:
        key = getattr(candidate, identity)
        previous = selected.get(key)
        if previous is None or (candidate.rank, query_position) < (
            previous[1].rank,
            previous[0],
        ):
            selected[key] = (query_position, candidate)


def _ordered_unique(
    selected: dict[str, tuple[int, PassageEvidence | VideoEvidence]], *, identity: str
) -> tuple[PassageEvidence, ...] | tuple[VideoEvidence, ...]:
    return tuple(
        item[1]
        for item in sorted(
            selected.values(),
            key=lambda item: (item[1].rank, item[0], getattr(item[1], identity)),
        )
    )


def _newest_publication_date(videos: tuple[VideoEvidence, ...]) -> date | None:
    known_dates = [item.published_at for item in videos if item.published_at is not None]
    return max(known_dates, default=None)


def _freshness(
    profile: FreshnessProfile,
    last_successful_discovery_at: datetime | None,
    now: datetime,
) -> FreshnessAssessment:
    if profile.maximum_age_days is None:
        return FreshnessAssessment(
            profile, None, last_successful_discovery_at, False, "refresh_not_required"
        )
    if last_successful_discovery_at is None:
        return FreshnessAssessment(profile, profile.maximum_age_days, None, True, "never_checked")
    stale = now - last_successful_discovery_at > timedelta(days=profile.maximum_age_days)
    return FreshnessAssessment(
        profile,
        profile.maximum_age_days,
        last_successful_discovery_at,
        stale,
        "stale" if stale else "fresh",
    )
