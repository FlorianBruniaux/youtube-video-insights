"""Stable, dependency-free values used by the research workflow."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from numbers import Real
from typing import TypeVar


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_T = TypeVar("_T")


class FreshnessProfile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    STABLE = "stable"
    HISTORICAL = "historical"

    @property
    def maximum_age_days(self) -> int | None:
        return {
            self.FAST: 14,
            self.STANDARD: 30,
            self.STABLE: 90,
            self.HISTORICAL: None,
        }[self]


class ResearchState(StrEnum):
    ASSESSING = "assessing"
    AWAITING_SUFFICIENCY = "awaiting_sufficiency_confirmation"
    DISCOVERING = "discovering"
    AWAITING_CANDIDATES = "awaiting_candidate_approval"
    ACQUIRING = "acquiring"
    REINDEXING = "reindexing"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    CANCELLED = "cancelled"


class RequiredUserAction(StrEnum):
    CONFIRM_SUFFICIENCY_OR_REFRESH = "confirm_sufficiency_or_refresh"
    APPROVE_CANDIDATES_OR_CANCEL = "approve_candidates_or_cancel"


class CandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    ACQUIRED = "acquired"
    ALREADY_PRESENT = "already_present"
    NO_TRANSCRIPT = "no_transcript"
    FAILED_RETRYABLE = "failed_retryable"


def normalize_research_text(value: str) -> str:
    """Return the stable identity form for a topic or explicit query."""
    _validate_research_text(value, label="research text")
    normalized = unicodedata.normalize("NFKC", value).strip()
    return " ".join(normalized.split()).casefold()


def discovery_fingerprint(
    *,
    topic: str,
    queries: tuple["QuerySpec", ...],
    languages: tuple[str, ...],
    provider_name: str,
    provider_version: int,
) -> str:
    """Build the exact discovery identity used by freshness checks."""
    _validate_research_text(topic, label="topic")
    _require_query_tuple(queries)
    _require_tuple(languages, label="languages")
    if not isinstance(provider_name, str) or not provider_name:
        raise ValueError("provider name must be a non-empty string")
    if isinstance(provider_version, bool) or not isinstance(provider_version, int):
        raise TypeError("provider version must be an integer")

    payload = {
        "languages": list(languages),
        "provider_name": provider_name,
        "provider_version": provider_version,
        "queries": [normalize_research_text(query.text) for query in queries],
        "topic": normalize_research_text(topic),
    }
    canonical_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class QuerySpec:
    text: str

    def __post_init__(self) -> None:
        _validate_research_text(self.text, label="query")


@dataclass(frozen=True, slots=True)
class DatabaseSnapshot:
    search_generation: str
    catalog_generation: str


@dataclass(frozen=True, slots=True)
class PassageEvidence:
    query: str
    passage_id: str
    video_id: str
    channel_id: str
    rank: int
    url: str
    excerpt: str
    source_sha256: str

    def __post_init__(self) -> None:
        _validate_video_id(self.video_id)
        _validate_finite_rank(self.rank)
        _validate_excerpt(self.excerpt)
        _validate_source_sha256(self.source_sha256)


@dataclass(frozen=True, slots=True)
class VideoEvidence:
    query: str
    video_id: str
    source_keys: tuple[str, ...]
    title: str
    published_at: date | None
    rank: int
    watch_url: str

    def __post_init__(self) -> None:
        _validate_video_id(self.video_id)
        _require_tuple(self.source_keys, label="source_keys")
        object.__setattr__(self, "published_at", _parse_date(self.published_at))
        _validate_finite_rank(self.rank)
        _validate_watch_url(self.video_id, self.watch_url)


@dataclass(frozen=True, slots=True)
class CoverageMetrics:
    matched_passages: int
    matched_videos: int
    distinct_channels: int
    queries_with_zero_hits: tuple[str, ...]
    newest_source_published_at: date | None
    unknown_publication_date_count: int

    def __post_init__(self) -> None:
        _require_tuple(self.queries_with_zero_hits, label="queries_with_zero_hits")
        object.__setattr__(
            self,
            "newest_source_published_at",
            _parse_date(self.newest_source_published_at),
        )


@dataclass(frozen=True, slots=True)
class FreshnessAssessment:
    profile: FreshnessProfile
    maximum_age_days: int | None
    last_successful_discovery_at: datetime | None
    stale: bool
    reason: str

    def __post_init__(self) -> None:
        if self.maximum_age_days != self.profile.maximum_age_days:
            raise ValueError("maximum age must match the freshness profile")


@dataclass(frozen=True, slots=True)
class ResearchAssessment:
    created_at: datetime
    snapshot: DatabaseSnapshot
    coverage: CoverageMetrics
    freshness: FreshnessAssessment
    passages: tuple[PassageEvidence, ...]
    videos: tuple[VideoEvidence, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.passages, label="passages")
        _require_tuple(self.videos, label="videos")


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    video_id: str
    title: str
    channel_id: str | None
    channel_title: str | None
    published_at: date | None
    watch_url: str
    matched_queries: tuple[str, ...]
    original_rank: int
    status: CandidateStatus

    def __post_init__(self) -> None:
        _validate_video_id(self.video_id)
        object.__setattr__(self, "published_at", _parse_date(self.published_at))
        _validate_watch_url(self.video_id, self.watch_url)
        _require_tuple(self.matched_queries, label="matched_queries")
        _validate_finite_rank(self.original_rank)


@dataclass(frozen=True, slots=True)
class ResearchSession:
    session_id: str
    topic: str
    queries: tuple[QuerySpec, ...]
    languages: tuple[str, ...]
    freshness_profile: FreshnessProfile
    discovery_fingerprint: str
    state: ResearchState
    required_user_action: RequiredUserAction | None
    revision: int
    retry_target: ResearchState | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _validate_research_text(self.topic, label="topic")
        _require_query_tuple(self.queries)
        _require_tuple(self.languages, label="languages")


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    idempotency_key: str
    action: str
    payload_json: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AcquisitionAttempt:
    attempt_id: str
    idempotency_key: str
    session_id: str
    revision: int
    status: str
    video_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_tuple(self.video_ids, label="video_ids")
        for video_id in self.video_ids:
            _validate_video_id(video_id)


@dataclass(frozen=True, slots=True)
class ResearchAcquisitionOutcome:
    attempt_id: str
    video_id: str
    status: CandidateStatus
    error_code: str | None
    source_sha256: str | None

    def __post_init__(self) -> None:
        _validate_video_id(self.video_id)
        if self.source_sha256 is not None:
            _validate_source_sha256(self.source_sha256)


@dataclass(frozen=True, slots=True)
class EventRecord:
    event_id: int
    from_state: ResearchState | None
    to_state: ResearchState
    event_code: str
    payload_json: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class SessionHistory:
    assessments: tuple[ResearchAssessment, ...]
    decisions: tuple[DecisionRecord, ...]
    acquisition_attempts: tuple[AcquisitionAttempt, ...]
    acquisition_outcomes: tuple[ResearchAcquisitionOutcome, ...]
    events: tuple[EventRecord, ...]

    def __post_init__(self) -> None:
        _require_tuple(self.assessments, label="assessments")
        _require_tuple(self.decisions, label="decisions")
        _require_tuple(self.acquisition_attempts, label="acquisition_attempts")
        _require_tuple(self.acquisition_outcomes, label="acquisition_outcomes")
        _require_tuple(self.events, label="events")


def _validate_research_text(value: object, *, label: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError(f"{label} must not be empty")
    if len(trimmed) > 500:
        raise ValueError(f"{label} must contain at most 500 code points")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError(f"{label} must not contain control characters")


def _require_query_tuple(queries: tuple[QuerySpec, ...]) -> None:
    _require_tuple(queries, label="queries")
    if not 1 <= len(queries) <= 8:
        raise ValueError("queries must contain between 1 and 8 items")
    if not all(isinstance(query, QuerySpec) for query in queries):
        raise TypeError("queries must contain QuerySpec values")
    normalized_queries = [normalize_research_text(query.text) for query in queries]
    if len(set(normalized_queries)) != len(normalized_queries):
        raise ValueError("queries must not contain normalized duplicates")


def _require_tuple(value: object, *, label: str) -> tuple[_T, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{label} must be a tuple")
    return value


def _validate_video_id(value: object) -> None:
    if not isinstance(value, str) or _VIDEO_ID.fullmatch(value) is None:
        raise ValueError("video ID must be an 11-character YouTube identifier")


def _validate_watch_url(video_id: str, watch_url: object) -> None:
    expected = f"https://www.youtube.com/watch?v={video_id}"
    if watch_url != expected:
        raise ValueError("watch URL must be canonical for the video ID")


def _validate_finite_rank(rank: object) -> None:
    if isinstance(rank, bool) or not isinstance(rank, Real) or not math.isfinite(rank):
        raise ValueError("rank must be finite")


def _validate_excerpt(value: object) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1_500:
        raise ValueError("excerpt must be non-empty and at most 1500 code points")


def _validate_source_sha256(value: object) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("source SHA-256 must be lowercase hexadecimal")


def _parse_date(value: date | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("date must not include a time")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise TypeError("date must be an ISO date string or date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError("date must be an ISO date")
    return parsed
