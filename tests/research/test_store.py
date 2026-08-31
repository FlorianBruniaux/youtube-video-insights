from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
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


def _create(store: ResearchStore, *, session_id: str = SESSION_ID) -> object:
    return store.create_session(
        session_id=session_id,
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


def _candidate(*, video_id: str = VIDEO_ID, status: CandidateStatus = CandidateStatus.CANDIDATE) -> ResearchCandidate:
    return ResearchCandidate(
        video_id=video_id,
        title="Local inference",
        channel_id="channel-1",
        channel_title="Channel",
        published_at=date(2026, 8, 30),
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
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


def test_idempotent_decision_replay_returns_its_committed_result_after_progress(tmp_path: object) -> None:
    """A replay must return the decision result, not the session's later state."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    decided = store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())

    replayed = store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")

    assert decided.state is ResearchState.DISCOVERING
    assert replayed == decided
    assert store.get_session(SESSION_ID).state is ResearchState.AWAITING_CANDIDATES


def test_legacy_raw_decision_payload_replays_its_historical_result(tmp_path: object) -> None:
    """A v1 raw request row must replay the original transition after later progress."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    committed = store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="legacy-refresh")
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    database = tmp_path / "research.sqlite3"
    with sqlite3.connect(database) as connection:  # type: ignore[arg-type]
        connection.execute("UPDATE research_decisions SET payload_json = ? WHERE idempotency_key = ?", ('{"sufficient":false}', "legacy-refresh"))
    legacy_store = ResearchStore(database, now=lambda: NOW)  # type: ignore[arg-type]

    replayed = legacy_store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="legacy-refresh")

    assert replayed == committed
    assert replayed.state is ResearchState.DISCOVERING
    assert replayed.revision == 2
    with pytest.raises(ValueError, match="idempotency"):
        legacy_store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=True, idempotency_key="legacy-refresh")


def test_legacy_raw_approval_replays_its_historical_result(tmp_path: object) -> None:
    """Legacy approval uses its transition event code, not the decision action name."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    committed = store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=(VIDEO_ID,), idempotency_key="legacy-approve")
    store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt", attempt_id="attempt-1")
    store.record_acquisition_batch(SESSION_ID, expected_revision=4, attempt_id="attempt-1", outcomes=(ResearchAcquisitionOutcome("attempt-1", VIDEO_ID, CandidateStatus.ACQUIRED, None, "c" * 64),))
    database = tmp_path / "research.sqlite3"
    with sqlite3.connect(database) as connection:  # type: ignore[arg-type]
        connection.execute("UPDATE research_decisions SET payload_json = ? WHERE idempotency_key = ?", ('{"video_ids":["abc123DEF45"]}', "legacy-approve"))
    legacy_store = ResearchStore(database, now=lambda: NOW)  # type: ignore[arg-type]

    replayed = legacy_store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=(VIDEO_ID,), idempotency_key="legacy-approve")

    assert replayed == committed
    assert replayed.state is ResearchState.ACQUIRING
    with pytest.raises(ValueError, match="idempotency"):
        legacy_store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=("vid00000001",), idempotency_key="legacy-approve")


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
    with pytest.raises(ValueError, match="idempotency"):
        store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt-key", attempt_id="attempt-other")
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


def test_approve_candidates_rejects_more_than_five_ids(tmp_path: object) -> None:
    """Approving six discoveries must fail before acquisition can start."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    video_ids = tuple(f"vid0000000{number}" for number in range(1, 7))
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=tuple(_candidate(video_id=video_id) for video_id in video_ids), provider_name="p", provider_version=1, errors=())

    with pytest.raises(ValueError, match="between 1 and 5"):
        store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=video_ids, idempotency_key="approve-six")

    assert store.get_session(SESSION_ID).state is ResearchState.AWAITING_CANDIDATES


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
    failed = store.record_failure(SESSION_ID, expected_revision=0, retry_target=ResearchState.ASSESSING, error_code="provider_timeout")
    assert failed.state is ResearchState.FAILED_RETRYABLE
    assert failed.retry_target is ResearchState.ASSESSING
    with pytest.raises(ValueError, match="error code"):
        store.record_failure(SESSION_ID, expected_revision=1, retry_target=ResearchState.ASSESSING, error_code="é")
    assessing = store.retry(SESSION_ID, expected_revision=1, idempotency_key="retry")
    assert assessing.state is ResearchState.ASSESSING
    with pytest.raises(ValueError, match="transition"):
        store.cancel(SESSION_ID, expected_revision=2, idempotency_key="cancel-active")
    store.record_assessment(SESSION_ID, expected_revision=2, assessment=_assessment())
    with pytest.raises(ValueError, match="transition"):
        store.record_failure(SESSION_ID, expected_revision=3, retry_target=ResearchState.DISCOVERING, error_code="must_not_bypass_confirmation")
    awaiting = store.get_session(SESSION_ID)
    assert awaiting.state is ResearchState.AWAITING_SUFFICIENCY
    refreshing = store.decide_sufficiency(SESSION_ID, expected_revision=3, sufficient=False, idempotency_key="refresh")
    waiting = store.record_candidates(SESSION_ID, expected_revision=4, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    cancelled = store.cancel(SESSION_ID, expected_revision=5, idempotency_key="cancel")
    assert refreshing.state is ResearchState.DISCOVERING
    assert waiting.state is ResearchState.AWAITING_CANDIDATES
    assert cancelled.state is ResearchState.CANCELLED


def test_existing_v1_metadata_with_wrong_columns_is_rejected(tmp_path: object) -> None:
    """A schema version row alone cannot authorize reads from a different layout."""
    database = tmp_path / "research.sqlite3"
    table_names = (
        "research_sessions", "research_queries", "research_languages", "research_assessments",
        "research_candidates", "research_decisions", "research_acquisition_attempts",
        "research_acquisition_outcomes", "research_events",
    )
    with sqlite3.connect(database) as connection:  # type: ignore[arg-type]
        connection.execute("CREATE TABLE schema_meta(version INTEGER NOT NULL)")
        connection.execute("INSERT INTO schema_meta VALUES (1)")
        for table_name in table_names:
            connection.execute(f"CREATE TABLE {table_name}(wrong_column TEXT)")

    with pytest.raises(ValueError, match="schema"):
        ResearchStore(database)  # type: ignore[arg-type]


def test_existing_v1_schema_with_missing_unique_and_foreign_key_is_rejected(tmp_path: object) -> None:
    """Matching columns do not compensate for removed identity and cascade constraints."""
    store = _store(tmp_path)
    database = tmp_path / "research.sqlite3"
    del store
    with sqlite3.connect(database) as connection:  # type: ignore[arg-type]
        connection.execute("DROP TABLE research_queries")
        connection.execute(
            """CREATE TABLE research_queries(
                session_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
                query_text TEXT NOT NULL, normalized_query TEXT NOT NULL
            )"""
        )

    with pytest.raises(ValueError, match="schema"):
        ResearchStore(database)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "replacement",
    [
        "topic TEXT NOT NULL CHECK (length(topic) > 0)",
        "topic TEXT NOT NULL DEFAULT ''",
    ],
)
def test_existing_v1_schema_with_hostile_table_sql_is_rejected(tmp_path: object, replacement: str) -> None:
    """A changed CHECK or DEFAULT is incompatible even when PRAGMA columns match."""
    store = _store(tmp_path)
    database = tmp_path / "research.sqlite3"
    del store
    with sqlite3.connect(database) as connection:  # type: ignore[arg-type]
        original_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'research_sessions'").fetchone()[0]
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE research_sessions")
        connection.execute(original_sql.replace("topic TEXT NOT NULL", replacement))

    with pytest.raises(ValueError, match="schema"):
        ResearchStore(database)  # type: ignore[arg-type]


def test_failure_only_retries_the_same_active_workflow_stage(tmp_path: object) -> None:
    """Each retryable stage can resume itself, but cannot jump ahead in the workflow."""
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    with pytest.raises(ValueError, match="retry target"):
        store.record_failure(SESSION_ID, expected_revision=2, retry_target=ResearchState.ACQUIRING, error_code="bad_jump")
    failed = store.record_failure(SESSION_ID, expected_revision=2, retry_target=ResearchState.DISCOVERING, error_code="discover_error")
    assert store.retry(SESSION_ID, expected_revision=3, idempotency_key="retry-discovery").state is ResearchState.DISCOVERING
    store.record_candidates(SESSION_ID, expected_revision=4, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    store.approve_candidates(SESSION_ID, expected_revision=5, video_ids=(VIDEO_ID,), idempotency_key="approve")
    failed = store.record_failure(SESSION_ID, expected_revision=6, retry_target=ResearchState.ACQUIRING, error_code="acquire_error")
    assert failed.retry_target is ResearchState.ACQUIRING
    assert store.retry(SESSION_ID, expected_revision=7, idempotency_key="retry-acquiring").state is ResearchState.ACQUIRING
    store.start_acquisition_attempt(SESSION_ID, expected_revision=8, video_ids=(VIDEO_ID,), idempotency_key="attempt", attempt_id="attempt-1")
    store.record_acquisition_batch(SESSION_ID, expected_revision=8, attempt_id="attempt-1", outcomes=(ResearchAcquisitionOutcome("attempt-1", VIDEO_ID, CandidateStatus.ACQUIRED, None, "c" * 64),))
    failed = store.record_failure(SESSION_ID, expected_revision=9, retry_target=ResearchState.REINDEXING, error_code="index_error")
    assert failed.retry_target is ResearchState.REINDEXING
    assert store.retry(SESSION_ID, expected_revision=10, idempotency_key="retry-reindex").state is ResearchState.REINDEXING


def test_discovery_order_uses_utc_not_offset_text_order(tmp_path: object) -> None:
    """A later UTC discovery with a lexically smaller local offset timestamp must win."""
    early_local = datetime(2026, 8, 31, 10, 30, tzinfo=timezone(timedelta(hours=2)))
    later_utc = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
    clock_values = [datetime(2026, 8, 31, 8, 0, tzinfo=UTC)] * 9 + [early_local]
    clock_values += [datetime(2026, 8, 31, 8, 0, tzinfo=UTC)] * 9 + [later_utc]
    values = iter(clock_values)
    store = ResearchStore(tmp_path / "research.sqlite3", now=lambda: next(values))  # type: ignore[operator]

    _create(store, session_id="01K4RESEARCH0000000000001")
    store.record_assessment("01K4RESEARCH0000000000001", expected_revision=0, assessment=_assessment())
    store.decide_sufficiency("01K4RESEARCH0000000000001", expected_revision=1, sufficient=False, idempotency_key="one")
    store.record_candidates("01K4RESEARCH0000000000001", expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())
    _create(store, session_id="01K4RESEARCH0000000000002")
    store.record_assessment("01K4RESEARCH0000000000002", expected_revision=0, assessment=_assessment())
    store.decide_sufficiency("01K4RESEARCH0000000000002", expected_revision=1, sufficient=False, idempotency_key="two")
    store.record_candidates("01K4RESEARCH0000000000002", expected_revision=2, candidates=(_candidate(),), provider_name="p", provider_version=1, errors=())

    assert store.last_successful_discovery_at("a" * 64) == later_utc


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
