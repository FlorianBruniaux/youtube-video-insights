from __future__ import annotations

from datetime import UTC, date, datetime
import sqlite3

import pytest

from yt_insights.research.models import (
    CandidateStatus,
    CoverageMetrics,
    DatabaseSnapshot,
    FreshnessAssessment,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchState,
    VideoEvidence,
)
from yt_insights.research.store import ResearchStore


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
SESSION_ID = "01K4RESEARCH0000000000000000"
VIDEO_ID = "abc123DEF45"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _store(tmp_path: object) -> ResearchStore:
    return ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW)  # type: ignore[operator]


def _create(store: ResearchStore) -> object:
    return store.create_session(
        session_id=SESSION_ID,
        topic="Local AI inference",
        queries=(QuerySpec("local LLM inference"),),
        languages=("en",),
        freshness_profile=FreshnessProfile.FAST,
        discovery_fingerprint="a" * 64,
    )


def _assessment() -> ResearchAssessment:
    passage = PassageEvidence(
        query="local LLM inference",
        passage_id="passage-1",
        video_id=VIDEO_ID,
        channel_id="channel-1",
        rank=1,
        url=WATCH_URL,
        excerpt="Local inference keeps models on-device.",
        source_sha256="b" * 64,
    )
    video = VideoEvidence(
        query="local LLM inference",
        video_id=VIDEO_ID,
        source_keys=("corpus",),
        title="Local inference",
        published_at=date(2026, 8, 30),
        rank=1,
        watch_url=WATCH_URL,
    )
    return ResearchAssessment(
        created_at=NOW,
        snapshot=DatabaseSnapshot("search-1", "catalog-1"),
        coverage=CoverageMetrics(1, 1, 1, (), date(2026, 8, 30), 0),
        freshness=FreshnessAssessment(FreshnessProfile.FAST, 14, None, False, "fresh"),
        passages=(passage,),
        videos=(video,),
    )


def _candidate(*, status: CandidateStatus = CandidateStatus.CANDIDATE) -> ResearchCandidate:
    return ResearchCandidate(
        video_id=VIDEO_ID,
        title="Local inference",
        channel_id="channel-1",
        channel_title="Channel",
        published_at=date(2026, 8, 30),
        watch_url=WATCH_URL,
        matched_queries=("local LLM inference",),
        original_rank=1,
        status=status,
    )


def test_new_database_creates_schema_session_and_ordered_inputs(tmp_path: object) -> None:
    """Removing schema/session inserts must break this lifecycle contract."""
    store = _store(tmp_path)

    session = _create(store)

    assert session.revision == 0
    assert session.state is ResearchState.ASSESSING
    assert session.queries == (QuerySpec("local LLM inference"),)
    assert session.languages == ("en",)
    with sqlite3.connect(tmp_path / "research.sqlite3") as connection:  # type: ignore[operator]
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("SELECT version FROM schema_meta").fetchall() == [(1,)]
    with pytest.raises(ValueError, match="session"):
        _create(store)
    assert store.get_session(SESSION_ID).revision == 0


def test_default_clock_persists_timezone_aware_timestamps(tmp_path: object) -> None:
    """A caller that does not inject a clock must still create a valid session."""
    store = ResearchStore(tmp_path / "research.sqlite3")  # type: ignore[operator]

    session = _create(store)

    assert session.created_at.tzinfo is not None
    assert session.created_at.utcoffset() is not None


def test_assessment_sufficiency_and_refresh_transitions_are_revision_checked(tmp_path: object) -> None:
    """Removing a state/revision guard must make one of these calls mutate incorrectly."""
    store = _store(tmp_path)
    _create(store)

    awaiting = store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    assert awaiting.state is ResearchState.AWAITING_SUFFICIENCY
    assert awaiting.revision == 1
    assert store.get_latest_assessment(SESSION_ID) == _assessment()
    with pytest.raises(ValueError, match="revision"):
        store.decide_sufficiency(SESSION_ID, expected_revision=0, sufficient=False, idempotency_key="refresh")
    discovering = store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    assert discovering.state is ResearchState.DISCOVERING
    assert discovering.revision == 2
    assert store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh") == discovering
    with pytest.raises(ValueError, match="idempotency"):
        store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=True, idempotency_key="refresh")


def test_sufficient_assessment_completes_without_discovery(tmp_path: object) -> None:
    """Changing the sufficient branch must not enter discovery."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())

    completed = store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=True, idempotency_key="complete")

    assert completed.state is ResearchState.COMPLETED
    assert completed.revision == 2


def test_candidate_acquisition_and_reindexing_lifecycle_is_durable(tmp_path: object) -> None:
    """Dropping durable candidate/outcome writes or a transition breaks this complete path."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")

    waiting = store.record_candidates(
        SESSION_ID,
        expected_revision=2,
        candidates=(_candidate(),),
        provider_name="provider",
        provider_version=1,
        errors=(),
    )
    assert waiting.state is ResearchState.AWAITING_CANDIDATES
    assert store.last_successful_discovery_at("a" * 64) == NOW
    acquiring = store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=(VIDEO_ID,), idempotency_key="approve")
    assert acquiring.state is ResearchState.ACQUIRING
    attempt = store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt-key", attempt_id="attempt-1")
    assert store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt-key", attempt_id="attempt-1") == attempt
    reindexing = store.record_acquisition_batch(
        SESSION_ID,
        expected_revision=4,
        attempt_id="attempt-1",
        outcomes=(ResearchAcquisitionOutcome("attempt-1", VIDEO_ID, CandidateStatus.ACQUIRED, None, "c" * 64),),
    )
    assert reindexing.state is ResearchState.REINDEXING
    assert store.list_candidates(SESSION_ID) == (_candidate(status=CandidateStatus.ACQUIRED),)
    assessing = store.complete_reindexing(SESSION_ID, expected_revision=5)
    assert assessing.state is ResearchState.ASSESSING
    assert assessing.revision == 6
    history = store.get_session_history(SESSION_ID)
    assert [decision.action for decision in history.decisions] == ["refresh", "approve_candidates"]
    assert history.acquisition_attempts[0].attempt_id == attempt.attempt_id
    assert history.acquisition_attempts[0].status == "completed"
    assert history.acquisition_outcomes[0].status is CandidateStatus.ACQUIRED
    assert [event.to_state for event in history.events] == [
        ResearchState.ASSESSING,
        ResearchState.AWAITING_SUFFICIENCY,
        ResearchState.DISCOVERING,
        ResearchState.AWAITING_CANDIDATES,
        ResearchState.ACQUIRING,
        ResearchState.ACQUIRING,
        ResearchState.REINDEXING,
        ResearchState.ASSESSING,
    ]


def test_invalid_transition_has_no_persisted_side_effect(tmp_path: object) -> None:
    """Writing an assessment from discovering must leave the previous history intact."""
    store = _store(tmp_path)
    _create(store)
    before = store.get_session_history(SESSION_ID)

    with pytest.raises(ValueError, match="transition"):
        store.record_candidates(SESSION_ID, expected_revision=0, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())

    assert store.get_session_history(SESSION_ID) == before
    assert store.get_session(SESSION_ID).revision == 0


def test_failure_retry_and_waiting_cancellation_use_only_allowed_targets(tmp_path: object) -> None:
    """Wrong retry target or cancellation from active work must not become a state change."""
    store = _store(tmp_path)
    _create(store)
    failed = store.record_failure(SESSION_ID, expected_revision=0, retry_target=ResearchState.DISCOVERING, error_code="provider_timeout")
    assert failed.state is ResearchState.FAILED_RETRYABLE
    assert failed.retry_target is ResearchState.DISCOVERING
    with pytest.raises(ValueError, match="error code"):
        store.record_failure(SESSION_ID, expected_revision=1, retry_target=ResearchState.DISCOVERING, error_code="é")
    discovering = store.retry(SESSION_ID, expected_revision=1, idempotency_key="retry")
    assert discovering.state is ResearchState.DISCOVERING
    with pytest.raises(ValueError, match="transition"):
        store.cancel(SESSION_ID, expected_revision=2, idempotency_key="cancel")
    waiting = store.record_candidates(SESSION_ID, expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    cancelled = store.cancel(SESSION_ID, expected_revision=3, idempotency_key="cancel")
    assert waiting.state is ResearchState.AWAITING_CANDIDATES
    assert cancelled.state is ResearchState.CANCELLED


def test_acquisition_outcome_rejects_unbounded_error_text_without_mutating(tmp_path: object) -> None:
    """Persisting exception text must not advance an active acquisition attempt."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=(VIDEO_ID,), idempotency_key="approve")
    store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt", attempt_id="attempt-1")

    with pytest.raises(ValueError, match="error code"):
        store.record_acquisition_batch(
            SESSION_ID,
            expected_revision=4,
            attempt_id="attempt-1",
            outcomes=(ResearchAcquisitionOutcome("attempt-1", VIDEO_ID, CandidateStatus.FAILED_RETRYABLE, "network error: é", None),),
        )

    assert store.get_session(SESSION_ID).state is ResearchState.ACQUIRING
    assert store.get_session_history(SESSION_ID).acquisition_attempts[0].status == "running"


def test_replaced_database_file_is_rejected_before_a_read(tmp_path: object) -> None:
    """Replacing the local state file must fail closed rather than read another database."""
    store = _store(tmp_path)
    _create(store)
    database = tmp_path / "research.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    with sqlite3.connect(replacement) as connection:
        connection.execute("CREATE TABLE replacement_only(value TEXT)")
    replacement.replace(database)

    with pytest.raises(RuntimeError, match="identity"):
        store.get_session(SESSION_ID)
