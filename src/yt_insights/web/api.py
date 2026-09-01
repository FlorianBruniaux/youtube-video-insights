"""Strict public request parsing and path-free response projections."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import parse_qs, urlsplit

from yt_insights.acquisition import (
    AcquisitionPlan,
    AcquisitionReport,
    SourceKind,
    build_acquisition_plan,
    classify_source,
    execute_acquisition,
)
from yt_insights.downloader import VideoListResult, fetch_video_list
from yt_insights.paths import DataPaths
from yt_insights.research.models import (
    FreshnessProfile,
    ResearchSession,
    normalize_research_text,
)
from yt_insights.search.models import SearchHit, SearchQuery
from yt_insights.web.jobs import JobSnapshot

_MAX_STRING = 500
_MAX_PAGE_SIZE = 100
_MAX_SEARCH_LIMIT = 20
_MIN_SQLITE_INTEGER = -(2**63)
_MAX_SQLITE_INTEGER = 2**63 - 1
_MAX_STRUCTURED_BYTES = 24 * 1024
_MAX_SAFE_PLAN_BYTES = 20 * 1024
_MAX_VIDEO_URL_LENGTH = 2_048
_MAX_SELECTED_VIDEOS = 1_000
_MAX_PLAN_IDENTITY_BYTES = 524_288
_MAX_RETAINED_IDENTITY_BYTES = 4_194_304
_MAX_PREPARED_PLANS = 100
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_JOB_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_VIDEO_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com"}
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LANGUAGE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PUBLIC_ACQUISITION_ERRORS = frozenset(
    {"acquisition_unavailable", "cache_read_failed", "download_failed", "no_transcript"}
)


class RequestValidationError(ValueError):
    """Raised without reflecting rejected public input."""


class PlanChanged(Exception):
    """Raised when confirmation does not match the current source plan."""


@dataclass(frozen=True, slots=True)
class Pagination:
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class StartSessionRequest:
    topic: str
    queries: tuple[str, ...]
    languages: tuple[str, ...]
    freshness_profile: FreshnessProfile
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    expected_revision: int
    decision: Literal["sufficient", "refresh"]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    expected_revision: int
    video_ids: tuple[str, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    expected_revision: int
    idempotency_key: str
    language: str


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    expected_revision: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CancellationRequest:
    expected_revision: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class RetryRequest:
    expected_revision: int
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ExportRequest:
    force: bool


@dataclass(frozen=True, slots=True)
class SourcePreviewRequest:
    source: str
    slug: str | None
    years: frozenset[int]
    language: str
    analyze: bool


@dataclass(frozen=True, slots=True)
class SourceAcquisitionRequest:
    fingerprint: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class _PreparedPlan:
    plan: AcquisitionPlan
    identity_size: int


def parse_search(query: Mapping[str, tuple[str, ...]]) -> SearchQuery:
    values = _query_object(query, {"q", "channel", "language", "limit"})
    text = _query_string(values, "q", required=True)
    if text is None:
        raise RequestValidationError()
    channel = _query_string(values, "channel")
    language = _query_string(values, "language")
    limit = _query_integer(
        values, "limit", default=10, minimum=1, maximum=_MAX_SEARCH_LIMIT
    )
    try:
        return SearchQuery(text, channel=channel, language=language, limit=limit)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError() from exc


def parse_pagination(query: Mapping[str, tuple[str, ...]]) -> Pagination:
    values = _query_object(query, {"limit", "offset"})
    return Pagination(
        _query_integer(values, "limit", default=20, minimum=1, maximum=_MAX_PAGE_SIZE),
        _query_integer(values, "offset", default=0, minimum=0),
    )


def parse_start_session(body: bytes) -> StartSessionRequest:
    payload = _json_object(
        body,
        {
            "topic",
            "queries",
            "languages",
            "freshness_profile",
            "idempotency_key",
        },
    )
    topic = _string(payload.get("topic"))
    queries = _string_list(payload.get("queries"), minimum=1, maximum=8)
    try:
        normalized_queries = tuple(normalize_research_text(query) for query in queries)
    except (TypeError, ValueError) as exc:
        raise RequestValidationError() from exc
    if len(set(normalized_queries)) != len(normalized_queries):
        raise RequestValidationError()
    languages = _string_list(payload.get("languages"), minimum=0, maximum=20)
    profile_text = _string(payload.get("freshness_profile"))
    try:
        profile = FreshnessProfile(profile_text)
    except ValueError as exc:
        raise RequestValidationError() from exc
    return StartSessionRequest(
        topic,
        queries,
        languages,
        profile,
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_decision(body: bytes) -> DecisionRequest:
    payload = _json_object(body, {"expected_revision", "decision", "idempotency_key"})
    revision = _revision(payload.get("expected_revision"))
    decision = _string(payload.get("decision"))
    if decision not in {"sufficient", "refresh"}:
        raise RequestValidationError()
    return DecisionRequest(
        revision,
        cast(Literal["sufficient", "refresh"], decision),
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_approval(body: bytes) -> ApprovalRequest:
    payload = _json_object(body, {"expected_revision", "video_ids", "idempotency_key"})
    raw_ids = payload.get("video_ids")
    if not isinstance(raw_ids, list):
        raise RequestValidationError()
    video_ids: list[str] = []
    seen: set[str] = set()
    for raw_id in raw_ids:
        video_id = _string(raw_id)
        if _VIDEO_ID.fullmatch(video_id) is None:
            raise RequestValidationError()
        if video_id not in seen:
            seen.add(video_id)
            video_ids.append(video_id)
    if not 1 <= len(video_ids) <= 5:
        raise RequestValidationError()
    return ApprovalRequest(
        _revision(payload.get("expected_revision")),
        tuple(video_ids),
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_acquisition(body: bytes) -> AcquisitionRequest:
    payload = _json_object(body, {"expected_revision", "idempotency_key", "language"})
    language = _string(payload.get("language")).lower()
    if _LANGUAGE.fullmatch(language) is None:
        raise RequestValidationError()
    return AcquisitionRequest(
        _revision(payload.get("expected_revision")),
        _idempotency_key(payload.get("idempotency_key")),
        language,
    )


def parse_discovery(body: bytes) -> DiscoveryRequest:
    payload = _json_object(body, {"expected_revision", "idempotency_key"})
    return DiscoveryRequest(
        _revision(payload.get("expected_revision")),
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_cancellation(body: bytes) -> CancellationRequest:
    payload = _json_object(body, {"expected_revision", "idempotency_key"})
    return CancellationRequest(
        _revision(payload.get("expected_revision")),
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_retry(body: bytes) -> RetryRequest:
    payload = _json_object(body, {"expected_revision", "idempotency_key"})
    return RetryRequest(
        _revision(payload.get("expected_revision")),
        _idempotency_key(payload.get("idempotency_key")),
    )


def parse_export(body: bytes) -> ExportRequest:
    payload = _json_object(body, {"force"})
    force = payload.get("force", False)
    if not isinstance(force, bool):
        raise RequestValidationError()
    return ExportRequest(force)


def parse_source_preview(body: bytes) -> SourcePreviewRequest:
    payload = _json_object(
        body,
        {"source", "slug", "years", "language", "analyze"},
        required={"source"},
    )
    return _source_preview(payload)


def parse_source_acquisition(body: bytes) -> SourceAcquisitionRequest:
    payload = _json_object(
        body,
        {"fingerprint", "idempotency_key"},
    )
    fingerprint = _string(payload.get("fingerprint"))
    if _SHA256.fullmatch(fingerprint) is None:
        raise RequestValidationError()
    return SourceAcquisitionRequest(
        fingerprint,
        _idempotency_key(payload.get("idempotency_key")),
    )


def validate_session_id(value: str) -> str:
    if not isinstance(value, str) or _SESSION_ID.fullmatch(value) is None:
        raise RequestValidationError()
    return value


def validate_job_id(value: str) -> str:
    if not isinstance(value, str) or _JOB_ID.fullmatch(value) is None:
        raise RequestValidationError()
    return value


def search_payload(hits: tuple[SearchHit, ...]) -> dict[str, object]:
    payloads = [_search_hit_payload(hit) for hit in hits]
    payload: dict[str, object] = {
        "hits": payloads,
        "returned": len(payloads),
        "truncated": False,
    }
    while payloads and _serialized_size(payload) >= _MAX_STRUCTURED_BYTES:
        payloads.pop()
        payload["returned"] = len(payloads)
        payload["truncated"] = True
    if _serialized_size(payload) >= _MAX_STRUCTURED_BYTES:
        raise RuntimeError("search response is unavailable")
    return payload


def research_session_payload(session: ResearchSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "topic": session.topic,
        "queries": [query.text for query in session.queries],
        "languages": list(session.languages),
        "freshness_profile": session.freshness_profile.value,
        "discovery_fingerprint": session.discovery_fingerprint,
        "state": session.state.value,
        "required_user_action": (
            None
            if session.required_user_action is None
            else session.required_user_action.value
        ),
        "revision": session.revision,
        "retry_target": None
        if session.retry_target is None
        else session.retry_target.value,
        "created_at": session.created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": session.updated_at.isoformat().replace("+00:00", "Z"),
    }


def job_payload(snapshot: JobSnapshot) -> dict[str, object]:
    return {
        "job_id": snapshot.job_id,
        "kind": snapshot.kind,
        "status": snapshot.status,
        "result": snapshot.result,
        "error_code": snapshot.error_code,
    }


class SourceAcquisitionFacade:
    """Build source plans through package services and disclose only safe fields."""

    def __init__(
        self,
        data_paths: DataPaths,
        *,
        classify: Callable[[str], SourceKind] = classify_source,
        fetch: Callable[..., VideoListResult] = fetch_video_list,
        build: Callable[..., AcquisitionPlan] = build_acquisition_plan,
        execute: Callable[..., AcquisitionReport] = execute_acquisition,
        max_selected_videos: int = _MAX_SELECTED_VIDEOS,
        max_plan_identity_bytes: int = _MAX_PLAN_IDENTITY_BYTES,
        max_retained_identity_bytes: int = _MAX_RETAINED_IDENTITY_BYTES,
        max_prepared_plans: int = _MAX_PREPARED_PLANS,
    ) -> None:
        capacities = (
            max_selected_videos,
            max_plan_identity_bytes,
            max_retained_identity_bytes,
            max_prepared_plans,
        )
        if any(
            isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1
            for capacity in capacities
        ):
            raise ValueError("prepared plan capacities must be positive integers")
        self._data_paths = data_paths
        self._classify = classify
        self._fetch = fetch
        self._build = build
        self._execute = execute
        self._max_selected_videos = max_selected_videos
        self._max_plan_identity_bytes = max_plan_identity_bytes
        self._max_retained_identity_bytes = max_retained_identity_bytes
        self._max_prepared_plans = max_prepared_plans
        self._plan_lock = threading.Lock()
        self._prepared_plans: OrderedDict[str, _PreparedPlan] = OrderedDict()
        self._retained_identity_bytes = 0

    def preview(self, request: SourcePreviewRequest) -> Mapping[str, object]:
        """Build a fresh preview and return no source or directory fields."""
        plan = self._plan(request)
        selected_ids = tuple(video.video_id for video in plan.selected_videos)
        if (
            len(plan.selected_videos) > self._max_selected_videos
            or len(plan.selected_urls) > self._max_selected_videos
            or not 0 <= plan.selected_count <= self._max_selected_videos
            or len(plan.selected_videos) != len(plan.selected_urls)
            or plan.selected_count != len(plan.selected_videos)
            or len(set(selected_ids)) != len(selected_ids)
            or not _selected_urls_match_video_ids(plan)
        ):
            return _plan_too_large()
        identity = _canonical_plan_bytes(plan)
        identity_size = len(identity)
        if (
            identity_size > self._max_plan_identity_bytes
            or identity_size > self._max_retained_identity_bytes
        ):
            return _plan_too_large()
        fingerprint = _fingerprint(identity)
        safe_plan = _safe_plan(plan, fingerprint=fingerprint)
        if "error" in safe_plan:
            return safe_plan
        with self._plan_lock:
            prior = self._prepared_plans.pop(fingerprint, None)
            if prior is not None:
                self._retained_identity_bytes -= prior.identity_size
            while self._prepared_plans and (
                len(self._prepared_plans) >= self._max_prepared_plans
                or self._retained_identity_bytes + identity_size
                > self._max_retained_identity_bytes
            ):
                _, evicted = self._prepared_plans.popitem(last=False)
                self._retained_identity_bytes -= evicted.identity_size
            self._prepared_plans[fingerprint] = _PreparedPlan(plan, identity_size)
            self._retained_identity_bytes += identity_size
        return safe_plan

    def prepare_acquisition(
        self, fingerprint: str
    ) -> Callable[[], Mapping[str, object]]:
        """Retrieve an exact cached preview without invoking its provider again."""
        if _SHA256.fullmatch(fingerprint) is None:
            raise PlanChanged()
        with self._plan_lock:
            prepared = self._prepared_plans.get(fingerprint)
        if prepared is None:
            raise PlanChanged()

        def operation() -> Mapping[str, object]:
            plan = prepared.plan
            current = _plan_fingerprint(plan)
            if not hmac.compare_digest(current, fingerprint):
                return {"schema_version": 1, "error": {"code": "plan_changed"}}
            return _safe_acquisition_report(self._execute(plan))

        return operation

    def _plan(self, request: SourcePreviewRequest) -> AcquisitionPlan:
        expected_kind = self._classify(request.source)
        discovered = self._fetch(request.source)
        if not isinstance(discovered, VideoListResult):
            raise TypeError("source provider returned an invalid result")
        plan = self._build(
            source=request.source,
            data_paths=self._data_paths,
            slug=request.slug,
            years=set(request.years),
            language=request.language,
            analyze=request.analyze,
            discovered=discovered.videos,
            discovery_errors=discovered.errors,
        )
        if plan.source_kind is not expected_kind:
            raise RuntimeError("source classification changed")
        return plan


def _selected_urls_match_video_ids(plan: AcquisitionPlan) -> bool:
    """Require one unambiguous supported URL for every exact disclosed ID."""
    return all(
        isinstance(video.video_id, str)
        and _VIDEO_ID.fullmatch(video.video_id) is not None
        and _single_video_identity(url) == video.video_id
        for video, url in zip(
            plan.selected_videos,
            plan.selected_urls,
            strict=True,
        )
    )


def _single_video_identity(url: object) -> str | None:
    """Extract an exact video ID only from unambiguous supported URL forms."""
    if (
        not isinstance(url, str)
        or url != url.strip()
        or len(url) > _MAX_VIDEO_URL_LENGTH
        or any(unicodedata.category(character) == "Cc" for character in url)
    ):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        return None
    host = (parsed.hostname or "").lower()
    try:
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
            max_num_fields=20,
        )
    except ValueError:
        return None
    if any(
        key.lower() in {"v", "list"} and key not in {"v", "list"}
        for key in query
    ):
        return None
    video_values = tuple(
        value
        for key, values in query.items()
        if key == "v"
        for value in values
    )
    if "list" in query:
        return None

    video_id: str | None = None
    path = parsed.path.rstrip("/") or "/"
    if host in _YOUTUBE_VIDEO_HOSTS and path == "/watch":
        if len(video_values) == 1:
            video_id = video_values[0]
    elif host == "youtu.be" and re.fullmatch(r"/[A-Za-z0-9_-]{11}", path):
        if not video_values:
            video_id = path[1:]
    elif host in _YOUTUBE_VIDEO_HOSTS:
        path_match = re.fullmatch(r"/(?:shorts|live)/([A-Za-z0-9_-]{11})", path)
        if path_match is not None and not video_values:
            video_id = path_match.group(1)
    if video_id is None or _VIDEO_ID.fullmatch(video_id) is None:
        return None
    return video_id


def _query_object(
    query: Mapping[str, tuple[str, ...]], allowed: set[str]
) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(query, Mapping) or any(key not in allowed for key in query):
        raise RequestValidationError()
    for key, values in query.items():
        if not isinstance(key, str) or not isinstance(values, tuple):
            raise RequestValidationError()
        if len(values) != 1 or not isinstance(values[0], str):
            raise RequestValidationError()
    return query


def _query_string(
    query: Mapping[str, tuple[str, ...]], key: str, *, required: bool = False
) -> str | None:
    values = query.get(key)
    if values is None:
        if required:
            raise RequestValidationError()
        return None
    return _string(values[0])


def _query_integer(
    query: Mapping[str, tuple[str, ...]],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    values = query.get(key)
    if values is None:
        return default
    raw = values[0]
    upper_bound = _MAX_SQLITE_INTEGER if maximum is None else maximum
    if len(raw) > len(str(upper_bound)):
        raise RequestValidationError()
    if re.fullmatch(r"0|[1-9][0-9]*", raw) is None:
        raise RequestValidationError()
    value = int(raw)
    if value < minimum or value > upper_bound:
        raise RequestValidationError()
    return value


def _json_object(
    body: bytes,
    allowed: set[str],
    *,
    required: set[str] | None = None,
) -> dict[str, object]:
    if not isinstance(body, bytes):
        raise RequestValidationError()

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise RequestValidationError()
            parsed[key] = value
        return parsed

    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_int=_json_integer,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestValidationError() from exc
    if not isinstance(payload, dict) or any(
        not isinstance(key, str) or key not in allowed for key in payload
    ):
        raise RequestValidationError()
    required_keys = allowed if required is None else required
    if any(key not in payload for key in required_keys):
        raise RequestValidationError()
    return payload


def _json_integer(raw: str) -> int:
    negative = raw.startswith("-")
    digits = raw[1:] if negative else raw
    if len(digits) > 19:
        raise RequestValidationError()
    boundary = str(-_MIN_SQLITE_INTEGER if negative else _MAX_SQLITE_INTEGER)
    if len(digits) == len(boundary) and digits > boundary:
        raise RequestValidationError()
    return int(raw)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise RequestValidationError()
    stripped = value.strip()
    if (
        not stripped
        or len(stripped) > _MAX_STRING
        or any(unicodedata.category(character) == "Cc" for character in value)
    ):
        raise RequestValidationError()
    return stripped


def _string_list(value: object, *, minimum: int, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise RequestValidationError()
    parsed = tuple(_string(item) for item in value)
    if len(set(parsed)) != len(parsed):
        raise RequestValidationError()
    return parsed


def _revision(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= _MAX_SQLITE_INTEGER
    ):
        raise RequestValidationError()
    return value


def _idempotency_key(value: object) -> str:
    parsed = _string(value)
    if len(parsed) > 200 or any(
        not 0x20 <= ord(character) <= 0x7E for character in parsed
    ):
        raise RequestValidationError()
    return parsed


def _source_preview(payload: Mapping[str, object]) -> SourcePreviewRequest:
    source = _string(payload.get("source"))
    raw_slug = payload.get("slug")
    slug = None if raw_slug is None else _string(raw_slug)
    raw_years = payload.get("years", [])
    if not isinstance(raw_years, list) or len(raw_years) > 50:
        raise RequestValidationError()
    years: set[int] = set()
    for year in raw_years:
        if (
            isinstance(year, bool)
            or not isinstance(year, int)
            or not 1900 <= year <= 9999
        ):
            raise RequestValidationError()
        years.add(year)
    language = _string(payload.get("language", "fr")).lower()
    if _LANGUAGE.fullmatch(language) is None:
        raise RequestValidationError()
    analyze = payload.get("analyze", False)
    if not isinstance(analyze, bool):
        raise RequestValidationError()
    return SourcePreviewRequest(source, slug, frozenset(years), language, analyze)


def _clip(value: str, maximum: int) -> str:
    return value if len(value) <= maximum else f"{value[: maximum - 1]}…"


def _search_hit_payload(hit: SearchHit) -> dict[str, object]:
    return {
        "passage_id": hit.passage.passage_id,
        "rank": hit.rank,
        "score": hit.score,
        "channel_id": _clip(hit.document.channel_id or "", 200),
        "channel": _clip(hit.document.channel_title, 200),
        "title": _clip(hit.document.video_title, 300),
        "language": _clip(hit.document.language, 64),
        "excerpt": _clip(hit.excerpt or "", 1_500),
        "start_seconds": hit.passage.start_seconds,
        "end_seconds": hit.passage.end_seconds,
        "url": hit.passage.youtube_url,
    }


def _serialized_size(payload: Mapping[str, object]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _plan_identity(plan: AcquisitionPlan) -> dict[str, object]:
    return {
        "source": plan.source,
        "source_kind": plan.source_kind.value,
        "output_root": str(plan.output_root),
        "transcripts_dir": str(plan.transcripts_dir),
        "insights_dir": str(plan.insights_dir),
        "data_paths": {
            "root": str(plan.data_paths.root),
            "transcripts": str(plan.data_paths.transcripts),
            "insights": str(plan.data_paths.insights),
            "shorts": str(plan.data_paths.shorts),
            "clips": str(plan.data_paths.clips),
            "exports": str(plan.data_paths.exports),
            "catalog_database": str(plan.data_paths.catalog_database),
            "search_database": str(plan.data_paths.search_database),
            "research_database": str(plan.data_paths.research_database),
        },
        "selected_videos": [
            {
                "video_id": video.video_id,
                "title": video.title,
                "upload_date": video.upload_date,
                "channel_id": video.channel_id,
                "channel_title": video.channel_title,
            }
            for video in plan.selected_videos
        ],
        "selected_urls": list(plan.selected_urls),
        "selected_count": plan.selected_count,
        "language": plan.language,
        "analyze": plan.analyze,
        "requires_confirmation": plan.requires_confirmation,
        "exclusions": list(plan.exclusions),
        "discovery_errors": list(plan.discovery_errors),
    }


def _plan_fingerprint(plan: AcquisitionPlan) -> str:
    return _fingerprint(_canonical_plan_bytes(plan))


def _canonical_plan_bytes(plan: AcquisitionPlan) -> bytes:
    return json.dumps(
        _plan_identity(plan),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _fingerprint(identity: bytes) -> str:
    return hashlib.sha256(identity).hexdigest()


def _plan_too_large() -> dict[str, object]:
    return {"schema_version": 1, "error": {"code": "plan_too_large"}}


def _safe_plan(
    plan: AcquisitionPlan, *, fingerprint: str | None = None
) -> dict[str, object]:
    videos = [
        {
            "video_id": video.video_id,
            "title": _clip(video.title, 300),
            "published_at": video.formatted_date,
            "url": video.watch_url,
        }
        for video in plan.selected_videos
    ]
    payload: dict[str, object] = {
        "fingerprint": fingerprint or _plan_fingerprint(plan),
        "source_kind": plan.source_kind.value,
        "selected_count": plan.selected_count,
        "video_ids": [video.video_id for video in plan.selected_videos],
        "videos": videos,
        "videos_returned": len(videos),
        "videos_truncated": False,
        "language": plan.language,
        "analyze": plan.analyze,
        "requires_confirmation": plan.requires_confirmation,
        "excluded_count": len(plan.exclusions),
        "discovery_error_count": len(plan.discovery_errors),
    }
    while videos and _serialized_size(payload) >= _MAX_SAFE_PLAN_BYTES:
        videos.pop()
        payload["videos_returned"] = len(videos)
        payload["videos_truncated"] = True
    if _serialized_size(payload) >= _MAX_SAFE_PLAN_BYTES:
        return _plan_too_large()
    return payload


def _safe_acquisition_report(report: AcquisitionReport) -> dict[str, object]:
    return {
        "selected": report.selected,
        "transcripts_ready": report.transcripts_ready,
        "insights_ready": report.insights_ready,
        "failure_count": len(report.failures),
        "exclusion_count": len(report.exclusions),
        "items": [
            {
                "video_id": item.video_id,
                "status": item.status.value,
                "error_code": (
                    item.error_code
                    if item.error_code is None
                    or item.error_code in _PUBLIC_ACQUISITION_ERRORS
                    else "acquisition_failed"
                ),
                "source_sha256": (
                    item.source_sha256
                    if item.source_sha256 is None
                    or _SHA256.fullmatch(item.source_sha256) is not None
                    else None
                ),
            }
            for item in report.items
        ],
        "exit_code": report.exit_code,
    }


def safe_export_payload(
    directory: Path,
    manifest_sha256: str,
    dossier_sha256: str,
) -> dict[str, object]:
    if (
        _SHA256.fullmatch(manifest_sha256) is None
        or _SHA256.fullmatch(dossier_sha256) is None
    ):
        raise RuntimeError("export result is invalid")
    name = directory.name
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise RuntimeError("export result is invalid")
    return {
        "name": name,
        "manifest_sha256": manifest_sha256,
        "dossier_sha256": dossier_sha256,
    }
