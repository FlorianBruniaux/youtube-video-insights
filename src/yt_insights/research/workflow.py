"""Application service for the durable local-first research workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from yt_insights.acquisition import rebuild_and_publish_indexes
from yt_insights.paths import DataPaths

from .acquisition import ResearchAcquisitionService
from .assessment import AssessmentRetryableError, EvidenceReader, assess_local
from .discovery import DiscoveryProvider, DiscoveryResult
from .dossier import DossierExportRequest, DossierExportResult, export_dossier
from .models import (
    AcquisitionAttempt,
    CandidateStatus,
    FreshnessProfile,
    QuerySpec,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchPublicSnapshot,
    ResearchSession,
    ResearchState,
    RetryReservation,
    discovery_fingerprint,
    normalize_research_text,
)
from .store import ResearchRevisionConflict, ResearchStore

_SCHEMA_VERSION = 1
_DISCOVERY_PROVIDER_NAME = "yt-dlp"
_DISCOVERY_PROVIDER_VERSION = 1
_MAX_PUBLIC_ACQUISITION_ATTEMPTS = 100
_PUBLIC_RESEARCH_ERROR_CODES = frozenset(
    {
        "acquisition_in_progress",
        "acquisition_unavailable",
        "discovery_unavailable",
        "index_refresh_failed",
        "local_index_unavailable",
        "partial_acquisition_failed",
        "retry_in_progress",
    }
)
_PUBLIC_ACQUISITION_ERROR_CODES = frozenset(
    {
        "acquisition_unavailable",
        "cache_read_failed",
        "download_failed",
        "no_transcript",
    }
)


@dataclass(frozen=True, slots=True)
class PublicAcquisitionItem:
    """Secret-free projection of one stored acquisition outcome."""

    video_id: str
    status: CandidateStatus
    error_code: str | None
    source_sha256: str | None


@dataclass(frozen=True, slots=True)
class PublicAcquisitionAttempt:
    """Secret-free projection of one stored acquisition attempt."""

    attempt_id: str
    status: str
    items: tuple[PublicAcquisitionItem, ...]


@dataclass(frozen=True, slots=True)
class ResearchResponse:
    """Bounded public snapshot of one research workflow operation."""

    session: ResearchSession
    assessment: ResearchAssessment | None
    candidates: tuple[ResearchCandidate, ...] | None
    error_code: str | None = None
    acquisition_history: tuple[PublicAcquisitionAttempt, ...] = ()
    acquisition_history_truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.acquisition_history, tuple):
            raise TypeError("acquisition history must be a tuple")
        if (
            self.error_code is not None
            and self.error_code not in _PUBLIC_RESEARCH_ERROR_CODES
        ):
            object.__setattr__(self, "error_code", "research_unavailable")
        safe_history = tuple(
            PublicAcquisitionAttempt(
                attempt.attempt_id,
                attempt.status,
                tuple(
                    PublicAcquisitionItem(
                        item.video_id,
                        item.status,
                        (
                            item.error_code
                            if item.error_code is None
                            or item.error_code in _PUBLIC_ACQUISITION_ERROR_CODES
                            else "acquisition_failed"
                        ),
                        item.source_sha256,
                    )
                    for item in attempt.items
                ),
            )
            for attempt in self.acquisition_history
        )
        object.__setattr__(self, "acquisition_history", safe_history)
        if len(self.acquisition_history) > _MAX_PUBLIC_ACQUISITION_ATTEMPTS:
            object.__setattr__(
                self,
                "acquisition_history",
                self.acquisition_history[-_MAX_PUBLIC_ACQUISITION_ATTEMPTS:],
            )
            object.__setattr__(self, "acquisition_history_truncated", True)

    @property
    def required_user_action(self) -> str | None:
        action = self.session.required_user_action
        return None if action is None else action.value

    def to_dict(self) -> dict[str, object]:
        """Return a stable, path-free payload suitable for JSON clients."""
        return {
            "schema_version": _SCHEMA_VERSION,
            "session": _session_payload(self.session),
            "assessment": None
            if self.assessment is None
            else _assessment_payload(self.assessment),
            "candidates": None
            if self.candidates is None
            else [_candidate_payload(candidate) for candidate in self.candidates],
            "required_user_action": self.required_user_action,
            "error_code": self.error_code,
            "acquisition_history": _acquisition_history_payload(
                self.acquisition_history
            ),
            "acquisition_history_truncated": self.acquisition_history_truncated,
        }


class ResearchWorkflow:
    """Coordinate local assessment and durable decisions without providers."""

    def __init__(
        self,
        *,
        store: ResearchStore,
        evidence_reader: EvidenceReader,
        discovery_provider: DiscoveryProvider | None = None,
        acquisition_service: ResearchAcquisitionService | None = None,
        data_paths: DataPaths | None = None,
        index_refresher: Callable[[DataPaths], object] = rebuild_and_publish_indexes,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        session_id_factory: Callable[[], str],
    ) -> None:
        self._store = store
        self._evidence_reader = evidence_reader
        self._discovery_provider = discovery_provider
        self._acquisition_service = acquisition_service
        self._data_paths = data_paths
        self._index_refresher = index_refresher
        self._now = now
        self._session_id_factory = session_id_factory

    def _response(
        self,
        session: ResearchSession,
        error_code: str | None = None,
    ) -> ResearchResponse:
        try:
            snapshot = self._store.get_public_snapshot(
                session.session_id,
                expected_revision=session.revision,
            )
        except ResearchRevisionConflict:
            snapshot = self._store.get_public_snapshot(session.session_id)
            if snapshot.session.revision <= session.revision:
                raise
        return self._response_from_snapshot(snapshot, error_code=error_code)

    def _response_from_snapshot(
        self,
        snapshot: ResearchPublicSnapshot,
        *,
        error_code: str | None = None,
    ) -> ResearchResponse:
        return ResearchResponse(
            snapshot.session,
            snapshot.assessment,
            snapshot.candidates or None,
            error_code,
            _public_acquisition_history(
                snapshot.acquisition_attempts,
                snapshot.acquisition_outcomes,
            ),
            snapshot.acquisition_history_truncated,
        )

    def start(
        self,
        *,
        topic: str,
        queries: tuple[str, ...],
        languages: tuple[str, ...],
        freshness_profile: FreshnessProfile,
    ) -> ResearchResponse:
        """Persist a bounded local assessment, never resolving a provider."""
        persisted_topic, query_specs, validated_languages = validate_start_request(
            topic=topic,
            queries=queries,
            languages=languages,
            freshness_profile=freshness_profile,
        )
        fingerprint = discovery_fingerprint(
            topic=persisted_topic,
            queries=query_specs,
            languages=validated_languages,
            provider_name=_DISCOVERY_PROVIDER_NAME,
            provider_version=_DISCOVERY_PROVIDER_VERSION,
        )
        session = self._store.create_session(
            session_id=self._session_id_factory(),
            topic=persisted_topic,
            queries=query_specs,
            languages=validated_languages,
            freshness_profile=freshness_profile,
            discovery_fingerprint=fingerprint,
        )
        try:
            assessment = assess_local(
                queries=query_specs,
                profile=freshness_profile,
                evidence_reader=self._evidence_reader,
                last_successful_discovery_at=self._store.last_successful_discovery_at(
                    fingerprint
                ),
                now=_utc_now(self._now),
                languages=validated_languages,
            )
        except AssessmentRetryableError:
            failed = self._store.record_failure(
                session.session_id,
                expected_revision=session.revision,
                retry_target=ResearchState.ASSESSING,
                error_code="local_index_unavailable",
            )
            return self._response(failed, "local_index_unavailable")

        stored = self._store.record_assessment(
            session.session_id,
            expected_revision=session.revision,
            assessment=assessment,
        )
        return self._response(stored)

    def status(self, session_id: str) -> ResearchResponse:
        """Load a durable session and its latest bounded evidence snapshots."""
        return self._response_from_snapshot(
            self._store.get_public_snapshot(session_id)
        )

    def export(
        self,
        request: DossierExportRequest,
        *,
        package_version: str,
    ) -> DossierExportResult:
        """Delegate deterministic export and its state checks to the dossier service."""
        return export_dossier(
            request,
            store=self._store,
            package_version=package_version,
        )

    def decide(
        self,
        session_id: str,
        *,
        expected_revision: int,
        decision: Literal["sufficient", "refresh"],
        idempotency_key: str,
    ) -> ResearchResponse:
        """Persist a sufficiency decision without beginning discovery."""
        if decision not in {"sufficient", "refresh"}:
            raise ValueError("decision is invalid")
        session = self._store.decide_sufficiency(
            session_id,
            expected_revision=expected_revision,
            sufficient=decision == "sufficient",
            idempotency_key=idempotency_key,
        )
        return self._response(session)

    def discover(
        self, session_id: str, *, expected_revision: int
    ) -> ResearchResponse:
        """Run the separately authorized metadata-only candidate discovery."""
        session = self._store.get_session(session_id)
        if session.revision != expected_revision:
            raise ValueError("session revision is stale")
        if session.state is not ResearchState.DISCOVERING:
            raise ValueError("session is not awaiting discovery")
        provider = self._discovery_provider
        if provider is None:
            return self._record_discovery_failure(session_id, expected_revision)
        try:
            result = provider.discover(session.queries, limit=10)
        except Exception:
            return self._record_discovery_failure(session_id, expected_revision)
        if not _valid_discovery_result(result):
            return self._record_discovery_failure(session_id, expected_revision)
        if not result.candidates:
            return self._record_discovery_failure(session_id, expected_revision)
        stored = self._store.record_candidates(
            session_id,
            expected_revision=expected_revision,
            candidates=result.candidates,
            provider_name=result.provider_name,
            provider_version=result.provider_version,
            errors=result.errors,
        )
        return self._response(stored)

    def candidates(self, session_id: str) -> ResearchResponse:
        """Return the latest persisted candidate snapshot without discovery."""
        return self.status(session_id)

    def approve(
        self,
        session_id: str,
        *,
        expected_revision: int,
        video_ids: tuple[str, ...],
        idempotency_key: str,
    ) -> ResearchResponse:
        """Persist the exact candidate IDs selected for a later acquisition."""
        session = self._store.approve_candidates(
            session_id,
            expected_revision=expected_revision,
            video_ids=video_ids,
            idempotency_key=idempotency_key,
        )
        return self._response(session)

    def cancel(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchResponse:
        """Cancel candidate approval, or replay its exact prior cancellation."""
        session = self._store.get_session(session_id)
        if session.state not in {
            ResearchState.AWAITING_CANDIDATES,
            ResearchState.CANCELLED,
        }:
            raise ValueError("session is not awaiting candidate approval")
        session = self._store.cancel(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
        return self._response(session)

    def acquire(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> ResearchResponse:
        """Acquire only the current approved snapshot, then refresh and reassess."""
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency key is required")
        if not isinstance(language, str) or not language.strip():
            raise ValueError("language is required")
        if self._acquisition_service is None or self._data_paths is None:
            raise RuntimeError("research acquisition is not configured")

        session = self._store.get_session(session_id)
        history = self._store.get_session_history(session_id)
        prior_attempt = next(
            (
                attempt
                for attempt in history.acquisition_attempts
                if attempt.idempotency_key == idempotency_key
            ),
            None,
        )
        if prior_attempt is not None:
            if (
                prior_attempt.session_id != session_id
                or prior_attempt.revision != expected_revision
                or prior_attempt.attempt_id
                != _acquisition_attempt_id(
                    session_id,
                    expected_revision,
                    idempotency_key,
                    prior_attempt.video_ids,
                    language=language,
                    cookies_from_browser=cookies_from_browser,
                )
            ):
                raise ValueError("idempotency key payload differs")
            if prior_attempt.status in {
                "running",
                "failed_retryable",
                "completed",
            }:
                return self._acquisition_replay(prior_attempt)

        if session.revision != expected_revision:
            raise ValueError("session revision is stale")
        if session.state is not ResearchState.ACQUIRING:
            raise ValueError("session is not awaiting acquisition")
        approved = tuple(
            candidate
            for candidate in self._store.list_candidates(session_id)
            if candidate.status is CandidateStatus.APPROVED
        )
        if not 1 <= len(approved) <= 5:
            raise ValueError("session has no valid approved candidate batch")
        video_ids = tuple(candidate.video_id for candidate in approved)
        if prior_attempt is not None and prior_attempt.video_ids != video_ids:
            raise ValueError("idempotency key payload differs")
        attempt_id = (
            prior_attempt.attempt_id
            if prior_attempt is not None
            else _acquisition_attempt_id(
                session_id,
                expected_revision,
                idempotency_key,
                video_ids,
                language=language,
                cookies_from_browser=cookies_from_browser,
            )
        )
        reservation = self._store.start_acquisition_attempt(
            session_id,
            expected_revision=expected_revision,
            video_ids=video_ids,
            idempotency_key=idempotency_key,
            attempt_id=attempt_id,
            language=language,
            cookies_from_browser=cookies_from_browser,
        )
        if not reservation.claimed:
            return self._acquisition_replay(reservation.attempt)
        with self._store.acquisition_execution_lock(
            reservation.attempt.attempt_id
        ) as claimed:
            if not claimed:
                return self._acquisition_in_progress(reservation.attempt)
            return self._continue_acquisition(
                reservation.attempt,
                session=self._store.get_session(session_id),
            )

    def retry(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> ResearchResponse:
        """Resume a failed stage or a lock-free orphaned acquisition."""
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("idempotency key is required")
        with self._store.retry_execution_lock(
            session_id,
            idempotency_key,
        ) as retry_claimed:
            if not retry_claimed:
                return self._retry_in_progress(session_id)
            attempt = self._store.acquisition_attempt_for_retry(
                session_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            if attempt is not None:
                with self._store.acquisition_execution_lock(
                    attempt.attempt_id
                ) as acquisition_claimed:
                    if not acquisition_claimed:
                        return self._acquisition_in_progress(attempt)
                    reservation = self._store.claim_retry(
                        session_id,
                        expected_revision=expected_revision,
                        idempotency_key=idempotency_key,
                        acquisition_attempt_id=attempt.attempt_id,
                        reclaim_incomplete=True,
                    )
                    return self._resume_retry(reservation, idempotency_key)
            reservation = self._store.claim_retry(
                session_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                reclaim_incomplete=True,
            )
            return self._resume_retry(reservation, idempotency_key)

    def _resume_retry(
        self,
        reservation: RetryReservation,
        idempotency_key: str,
    ) -> ResearchResponse:
        session_id = reservation.session.session_id
        if not reservation.claimed:
            return self._response(
                reservation.session,
                reservation.error_code,
            )

        if reservation.finalize_only:
            response = self._response(
                reservation.session,
                reservation.error_code,
            )
        else:
            target = reservation.retry_target
            partial_attempt_id = self._partial_attempt_id(
                reservation.acquisition_attempt
            )
            if target is ResearchState.REINDEXING:
                response = self._finish_reindexing(
                    reservation.session,
                    refresh=True,
                    partial_attempt_id=partial_attempt_id,
                )
            elif target is ResearchState.ASSESSING:
                response = self._assess_session(
                    reservation.session,
                    partial_attempt_id=partial_attempt_id,
                )
            elif target is ResearchState.DISCOVERING:
                response = self.discover(
                    session_id,
                    expected_revision=reservation.session.revision,
                )
            elif target is ResearchState.ACQUIRING:
                attempt = reservation.acquisition_attempt
                if attempt is None:
                    raise ValueError("retry has no acquisition attempt")
                response = self._continue_acquisition(
                    attempt,
                    session=reservation.session,
                )
            else:
                raise ValueError("retry target is invalid")

        self._store.complete_retry(
            idempotency_key,
            result=response.session,
            error_code=response.error_code,
        )
        return response

    def _acquisition_replay(
        self,
        attempt: AcquisitionAttempt,
    ) -> ResearchResponse:
        session = self._store.get_session(attempt.session_id)
        partial_failure = self._partial_attempt_id(attempt) is not None
        error_code = (
            (
                "partial_acquisition_failed"
                if partial_failure
                else "acquisition_unavailable"
            )
            if attempt.status == "failed_retryable"
            and session.state is ResearchState.FAILED_RETRYABLE
            and session.retry_target is ResearchState.ACQUIRING
            else (
                "acquisition_in_progress" if attempt.status == "running" else None
            )
        )
        return self._response(
            session,
            error_code,
        )

    def _acquisition_in_progress(
        self,
        attempt: AcquisitionAttempt,
    ) -> ResearchResponse:
        session = self._store.get_session(attempt.session_id)
        return self._response(
            session,
            "acquisition_in_progress",
        )

    def _retry_in_progress(self, session_id: str) -> ResearchResponse:
        session = self._store.get_session(session_id)
        return self._response(
            session,
            "retry_in_progress",
        )

    def _continue_acquisition(
        self,
        attempt: AcquisitionAttempt,
        *,
        session: ResearchSession,
    ) -> ResearchResponse:
        """Finish one exclusively claimed attempt without repeating saved outcomes."""
        if self._acquisition_service is None or self._data_paths is None:
            raise RuntimeError("research acquisition is not configured")
        if session.session_id != attempt.session_id:
            raise ValueError("acquisition attempt belongs to another session")

        history = self._store.get_session_history(session.session_id)
        completed_ids = {
            outcome.video_id
            for outcome in history.acquisition_outcomes
            if outcome.attempt_id == attempt.attempt_id
            and outcome.status is not CandidateStatus.FAILED_RETRYABLE
        }
        candidates = {
            candidate.video_id: candidate
            for candidate in self._store.list_candidates(session.session_id)
        }

        for video_id in attempt.video_ids:
            if video_id in completed_ids:
                continue
            candidate = candidates.get(video_id)
            if candidate is None:
                return self._fail_acquisition(session, attempt)
            if candidate.status is not CandidateStatus.APPROVED:
                candidate = ResearchCandidate(
                    video_id=candidate.video_id,
                    title=candidate.title,
                    channel_id=candidate.channel_id,
                    channel_title=candidate.channel_title,
                    published_at=candidate.published_at,
                    watch_url=candidate.watch_url,
                    matched_queries=candidate.matched_queries,
                    original_rank=candidate.original_rank,
                    status=CandidateStatus.APPROVED,
                )
            try:
                acquired = self._acquisition_service.acquire_approved(
                    (candidate,),
                    data_paths=self._data_paths,
                    language=attempt.language,
                    cookies_from_browser=attempt.cookies_from_browser,
                )
                if len(acquired) != 1 or acquired[0].video_id != video_id:
                    raise ValueError(
                        "acquisition outcome does not match the approved video"
                    )
                raw_outcome = acquired[0]
                outcome = ResearchAcquisitionOutcome(
                    attempt.attempt_id,
                    raw_outcome.video_id,
                    raw_outcome.status,
                    raw_outcome.error_code,
                    raw_outcome.source_sha256,
                )
            except Exception:
                outcome = ResearchAcquisitionOutcome(
                    attempt.attempt_id,
                    video_id,
                    CandidateStatus.FAILED_RETRYABLE,
                    "acquisition_unavailable",
                    None,
                )
            self._store.record_acquisition_progress(
                session.session_id,
                expected_revision=session.revision,
                attempt_id=attempt.attempt_id,
                outcomes=(outcome,),
            )

        outcomes = tuple(
            outcome
            for outcome in self._store.get_session_history(
                session.session_id
            ).acquisition_outcomes
            if outcome.attempt_id == attempt.attempt_id
        )
        should_refresh = any(
            outcome.status
            in {CandidateStatus.ACQUIRED, CandidateStatus.ALREADY_PRESENT}
            for outcome in outcomes
        )
        has_retryable_failure = any(
            outcome.status is CandidateStatus.FAILED_RETRYABLE
            for outcome in outcomes
        )
        if (
            not should_refresh
            and has_retryable_failure
        ):
            return self._fail_acquisition(session, attempt)

        reindexing = self._store.complete_acquisition_attempt(
            session.session_id,
            expected_revision=session.revision,
            attempt_id=attempt.attempt_id,
        )
        return self._finish_reindexing(
            reindexing,
            refresh=should_refresh,
            partial_attempt_id=(
                attempt.attempt_id if has_retryable_failure else None
            ),
        )

    def _fail_acquisition(
        self,
        session: ResearchSession,
        attempt: AcquisitionAttempt,
    ) -> ResearchResponse:
        failed = self._store.record_acquisition_failure(
            session.session_id,
            expected_revision=session.revision,
            attempt_id=attempt.attempt_id,
            error_code="acquisition_unavailable",
        )
        return self._response(
            failed,
            "acquisition_unavailable",
        )

    def _finish_reindexing(
        self,
        session: ResearchSession,
        *,
        refresh: bool,
        partial_attempt_id: str | None = None,
    ) -> ResearchResponse:
        if self._data_paths is None:
            raise RuntimeError("research acquisition is not configured")
        if refresh:
            try:
                self._index_refresher(self._data_paths)
            except Exception:
                failed = self._store.record_failure(
                    session.session_id,
                    expected_revision=session.revision,
                    retry_target=ResearchState.REINDEXING,
                    error_code="index_refresh_failed",
                )
                return self._response(
                    failed,
                    "index_refresh_failed",
                )
        assessing = self._store.complete_reindexing(
            session.session_id,
            expected_revision=session.revision,
        )
        return self._assess_session(
            assessing,
            partial_attempt_id=partial_attempt_id,
        )

    def _assess_session(
        self,
        session: ResearchSession,
        *,
        partial_attempt_id: str | None = None,
    ) -> ResearchResponse:
        try:
            assessment = assess_local(
                queries=session.queries,
                profile=session.freshness_profile,
                evidence_reader=self._evidence_reader,
                last_successful_discovery_at=self._store.last_successful_discovery_at(
                    session.discovery_fingerprint
                ),
                now=_utc_now(self._now),
                languages=session.languages,
            )
        except AssessmentRetryableError:
            failed = self._store.record_failure(
                session.session_id,
                expected_revision=session.revision,
                retry_target=ResearchState.ASSESSING,
                error_code="local_index_unavailable",
            )
            return self._response(
                failed,
                "local_index_unavailable",
            )
        if partial_attempt_id is None:
            stored = self._store.record_assessment(
                session.session_id,
                expected_revision=session.revision,
                assessment=assessment,
            )
            error_code = None
        else:
            stored = self._store.record_partial_acquisition_assessment(
                session.session_id,
                expected_revision=session.revision,
                attempt_id=partial_attempt_id,
                assessment=assessment,
            )
            error_code = "partial_acquisition_failed"
        return self._response(
            stored,
            error_code,
        )

    def _partial_attempt_id(
        self,
        attempt: AcquisitionAttempt | None,
    ) -> str | None:
        if attempt is None:
            return None
        outcomes = tuple(
            outcome
            for outcome in self._store.get_session_history(
                attempt.session_id
            ).acquisition_outcomes
            if outcome.attempt_id == attempt.attempt_id
        )
        has_failure = any(
            outcome.status is CandidateStatus.FAILED_RETRYABLE
            for outcome in outcomes
        )
        has_ready_source = any(
            outcome.status
            in {CandidateStatus.ACQUIRED, CandidateStatus.ALREADY_PRESENT}
            for outcome in outcomes
        )
        return attempt.attempt_id if has_failure and has_ready_source else None

    def _record_discovery_failure(
        self, session_id: str, expected_revision: int
    ) -> ResearchResponse:
        failed = self._store.record_failure(
            session_id,
            expected_revision=expected_revision,
            retry_target=ResearchState.DISCOVERING,
            error_code="discovery_unavailable",
        )
        return self._response(
            failed,
            "discovery_unavailable",
        )


def validate_start_request(
    *,
    topic: str,
    queries: tuple[str, ...],
    languages: tuple[str, ...],
    freshness_profile: FreshnessProfile,
) -> tuple[str, tuple[QuerySpec, ...], tuple[str, ...]]:
    """Validate all caller-controlled values before a store or reader method."""
    # Validate the normalized identity without changing the user-visible topic.
    normalize_research_text(topic)
    if not isinstance(queries, tuple):
        raise TypeError("queries must be a tuple")
    query_specs = tuple(QuerySpec(query) for query in queries)
    # Re-use the durable model validation, including count and normalized duplicates.
    discovery_fingerprint(
        topic=topic,
        queries=query_specs,
        languages=languages,
        provider_name=_DISCOVERY_PROVIDER_NAME,
        provider_version=_DISCOVERY_PROVIDER_VERSION,
    )
    if not all(isinstance(language, str) and language for language in languages):
        raise ValueError("languages must contain non-empty strings")
    if len(set(languages)) != len(languages):
        raise ValueError("languages must not contain duplicates")
    if not isinstance(freshness_profile, FreshnessProfile):
        raise TypeError("freshness profile is invalid")
    return topic, query_specs, languages


def _utc_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is not UTC:
        raise ValueError("workflow clock must return timezone-aware UTC")
    return now


def _valid_discovery_result(value: object) -> bool:
    """Reject malformed provider output before durable state is changed."""
    return (
        isinstance(value, DiscoveryResult)
        and isinstance(value.provider_name, str)
        and bool(value.provider_name)
        and isinstance(value.provider_version, int)
        and not isinstance(value.provider_version, bool)
        and isinstance(value.candidates, tuple)
        and 1 <= len(value.candidates) <= 10
        and all(isinstance(candidate, ResearchCandidate) for candidate in value.candidates)
        and isinstance(value.errors, tuple)
        and all(isinstance(error, str) for error in value.errors)
        and isinstance(value.completed, bool)
    )


def _acquisition_attempt_id(
    session_id: str,
    expected_revision: int,
    idempotency_key: str,
    video_ids: tuple[str, ...],
    *,
    language: str,
    cookies_from_browser: str | None,
) -> str:
    payload = json.dumps(
        {
            "idempotency_key": idempotency_key,
            "language": language,
            "cookies_from_browser": cookies_from_browser,
            "revision": expected_revision,
            "session_id": session_id,
            "video_ids": list(video_ids),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "acq-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:28]


def _session_payload(session: ResearchSession) -> dict[str, object]:
    return {
        "session_id": session.session_id,
        "topic": session.topic,
        "queries": [query.text for query in session.queries],
        "languages": list(session.languages),
        "freshness_profile": session.freshness_profile.value,
        "discovery_fingerprint": session.discovery_fingerprint,
        "state": session.state.value,
        "revision": session.revision,
        "retry_target": None
        if session.retry_target is None
        else session.retry_target.value,
        "created_at": _timestamp(session.created_at),
        "updated_at": _timestamp(session.updated_at),
    }


def _assessment_payload(assessment: ResearchAssessment) -> dict[str, object]:
    return {
        "created_at": _timestamp(assessment.created_at),
        "snapshot": {
            "search_generation": assessment.snapshot.search_generation,
            "catalog_generation": assessment.snapshot.catalog_generation,
        },
        "coverage": {
            "matched_passages": assessment.coverage.matched_passages,
            "matched_videos": assessment.coverage.matched_videos,
            "distinct_channels": assessment.coverage.distinct_channels,
            "queries_with_zero_hits": list(assessment.coverage.queries_with_zero_hits),
            "newest_source_published_at": _date(assessment.coverage.newest_source_published_at),
            "unknown_publication_date_count": assessment.coverage.unknown_publication_date_count,
        },
        "freshness": {
            "profile": assessment.freshness.profile.value,
            "maximum_age_days": assessment.freshness.maximum_age_days,
            "last_successful_discovery_at": _timestamp(
                assessment.freshness.last_successful_discovery_at
            ),
            "stale": assessment.freshness.stale,
            "reason": assessment.freshness.reason,
        },
        "passages": [
            {
                "query": passage.query,
                "passage_id": passage.passage_id,
                "video_id": passage.video_id,
                "channel_id": passage.channel_id,
                "rank": passage.rank,
                "url": passage.url,
                "excerpt": passage.excerpt,
                "source_sha256": passage.source_sha256,
            }
            for passage in assessment.passages
        ],
        "videos": [
            {
                "query": video.query,
                "video_id": video.video_id,
                "source_keys": list(video.source_keys),
                "title": video.title,
                "published_at": _date(video.published_at),
                "rank": video.rank,
                "watch_url": video.watch_url,
            }
            for video in assessment.videos
        ],
    }


def _candidate_payload(candidate: ResearchCandidate) -> dict[str, object]:
    return {
        "video_id": candidate.video_id,
        "title": candidate.title,
        "channel_id": candidate.channel_id,
        "channel_title": candidate.channel_title,
        "published_at": _date(candidate.published_at),
        "watch_url": candidate.watch_url,
        "matched_queries": list(candidate.matched_queries),
        "original_rank": candidate.original_rank,
        "status": candidate.status.value,
    }


def _public_acquisition_history(
    attempts: tuple[AcquisitionAttempt, ...],
    outcomes: tuple[ResearchAcquisitionOutcome, ...],
) -> tuple[PublicAcquisitionAttempt, ...]:
    outcomes_by_attempt = {
        attempt.attempt_id: {
            outcome.video_id: outcome
            for outcome in outcomes
            if outcome.attempt_id == attempt.attempt_id
        }
        for attempt in attempts
    }
    return tuple(
        PublicAcquisitionAttempt(
            attempt.attempt_id,
            attempt.status,
            tuple(
                PublicAcquisitionItem(
                    outcome.video_id,
                    outcome.status,
                    outcome.error_code,
                    outcome.source_sha256,
                )
                for video_id in attempt.video_ids
                if (outcome := outcomes_by_attempt[attempt.attempt_id].get(video_id))
                is not None
            ),
        )
        for attempt in attempts
    )


def _acquisition_history_payload(
    attempts: tuple[PublicAcquisitionAttempt, ...],
) -> list[dict[str, object]]:
    return [
        {
            "attempt_id": attempt.attempt_id,
            "status": attempt.status,
            "items": [
                {
                    "video_id": item.video_id,
                    "status": item.status.value,
                    "error_code": item.error_code,
                    "source_sha256": item.source_sha256,
                }
                for item in attempt.items
            ],
        }
        for attempt in attempts
    ]


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()
