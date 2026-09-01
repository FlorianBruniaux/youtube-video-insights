from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone

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
from yt_insights.research.store import (
    DecisionReplayStatus,
    ResearchIdempotencyConflict,
    ResearchRevisionConflict,
    ResearchStore,
)

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


def _prepare_acquisition_replay(store: ResearchStore) -> None:
    _create(store)
    store.record_assessment(
        SESSION_ID,
        expected_revision=0,
        assessment=_assessment(),
    )
    store.decide_sufficiency(
        SESSION_ID,
        expected_revision=1,
        sufficient=False,
        idempotency_key="refresh",
    )
    store.record_candidates(
        SESSION_ID,
        expected_revision=2,
        candidates=(_candidate(),),
        provider_name="provider",
        provider_version=1,
        errors=(),
    )
    store.approve_candidates(
        SESSION_ID,
        expected_revision=3,
        video_ids=(VIDEO_ID,),
        idempotency_key="approve",
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


def test_list_sessions_is_bounded_and_stably_sorted(tmp_path: object) -> None:
    """Removing ordering, paging, or the store guard breaks this public read."""
    moments = iter((
        datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
        datetime(2026, 8, 31, 10, 1, tzinfo=UTC),
        datetime(2026, 8, 31, 10, 1, tzinfo=UTC),
        datetime(2026, 8, 31, 10, 2, tzinfo=UTC),
    ))
    store = ResearchStore(tmp_path / "research.sqlite3", now=lambda: next(moments))  # type: ignore[operator]
    first = store.create_session(
        session_id="a" * 32,
        topic="Older",
        queries=(QuerySpec("older"),),
        languages=("en",),
        freshness_profile=FreshnessProfile.FAST,
        discovery_fingerprint="a" * 64,
    )
    second = store.create_session(
        session_id="b" * 32,
        topic="Newer",
        queries=(QuerySpec("newer"),),
        languages=("en",),
        freshness_profile=FreshnessProfile.FAST,
        discovery_fingerprint="b" * 64,
    )

    assert store.list_sessions(limit=1, offset=0) == (second,)
    assert store.list_sessions(limit=1, offset=1) == (first,)
    with pytest.raises(ValueError, match="limit"):
        store.list_sessions(limit=0, offset=0)
    with pytest.raises(ValueError, match="offset"):
        store.list_sessions(limit=1, offset=-1)


def test_public_timeline_returns_latest_bounded_decisions_and_events(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loading unbounded history would let one old session exhaust the web read."""
    store = _store(tmp_path)
    _create(store)
    database = tmp_path / "research.sqlite3"  # type: ignore[operator]
    with sqlite3.connect(database) as connection:
        for index in range(3):
            created_at = f"2026-08-31T10:0{index + 1}:00+00:00"
            connection.execute(
                "INSERT INTO research_decisions VALUES (?, ?, ?, ?, ?, ?)",
                (
                    f"decision-{index}",
                    SESSION_ID,
                    index,
                    f"action-{index}",
                    "{}",
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO research_events(session_id, from_state, to_state, "
                "event_code, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    SESSION_ID,
                    ResearchState.ASSESSING.value,
                    ResearchState.ASSESSING.value,
                    f"event-{index}",
                    "{}",
                    created_at,
                ),
            )

    original_connection = store._connection

    @contextmanager
    def payload_guarded_connection():
        with original_connection() as connection:
            def authorize(
                action: int,
                table: str | None,
                column: str | None,
                database: str | None,
                trigger: str | None,
            ) -> int:
                del database, trigger
                if (
                    action == sqlite3.SQLITE_READ
                    and table in {"research_decisions", "research_events"}
                    and column == "payload_json"
                ):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK

            connection.set_authorizer(authorize)
            yield connection

    monkeypatch.setattr(store, "_connection", payload_guarded_connection)

    timeline = store.get_public_timeline(
        SESSION_ID,
        expected_revision=0,
        limit=2,
    )

    assert [decision.action for decision in timeline.decisions] == [
        "action-1",
        "action-2",
    ]
    assert [event.event_code for event in timeline.events] == [
        "event-1",
        "event-2",
    ]
    assert timeline.decisions_truncated is True
    assert timeline.events_truncated is True
    with pytest.raises(ValueError, match="limit"):
        store.get_public_timeline(SESSION_ID, expected_revision=0, limit=0)
    with pytest.raises(ResearchRevisionConflict):
        store.get_public_timeline(SESSION_ID, expected_revision=1, limit=2)


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


def test_get_decision_replay_returns_only_the_indexed_persisted_result(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(
        SESSION_ID,
        expected_revision=0,
        assessment=_assessment(),
    )
    committed = store.decide_sufficiency(
        SESSION_ID,
        expected_revision=1,
        sufficient=False,
        idempotency_key="refresh",
    )
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    replayed = store.get_decision_replay(
        SESSION_ID,
        expected_revision=1,
        action="refresh",
        request={"sufficient": False},
        idempotency_key="refresh",
    )

    assert replayed is not None
    assert replayed.status is DecisionReplayStatus.COMPLETED
    assert replayed.session == committed
    assert (
        store.get_decision_replay(
            SESSION_ID,
            expected_revision=1,
            action="refresh",
            request={"sufficient": False},
            idempotency_key="missing",
        )
        is None
    )


def test_get_decision_replay_identifies_an_in_progress_retry_reservation(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _create(store)
    failed = store.record_failure(
        SESSION_ID,
        expected_revision=0,
        retry_target=ResearchState.ASSESSING,
        error_code="provider_timeout",
    )
    reservation = store.claim_retry(
        SESSION_ID,
        expected_revision=failed.revision,
        idempotency_key="retry-in-progress",
    )
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    replayed = store.get_decision_replay(
        SESSION_ID,
        expected_revision=failed.revision,
        action="retry",
        request={"expected_revision": failed.revision},
        idempotency_key="retry-in-progress",
    )

    assert replayed is not None
    assert replayed.status is DecisionReplayStatus.RETRY_IN_PROGRESS
    assert replayed.session == reservation.session


def test_get_decision_replay_rejects_an_in_progress_retry_request_mismatch(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    _create(store)
    failed = store.record_failure(
        SESSION_ID,
        expected_revision=0,
        retry_target=ResearchState.ASSESSING,
        error_code="provider_timeout",
    )
    store.claim_retry(
        SESSION_ID,
        expected_revision=failed.revision,
        idempotency_key="retry-in-progress",
    )

    with pytest.raises(ResearchIdempotencyConflict, match="idempotency"):
        store.get_decision_replay(
            SESSION_ID,
            expected_revision=failed.revision,
            action="retry",
            request={"expected_revision": failed.revision + 1},
            idempotency_key="retry-in-progress",
        )


def test_get_decision_replay_rejects_a_reused_key_with_changed_identity_or_payload(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(
        SESSION_ID,
        expected_revision=0,
        assessment=_assessment(),
    )
    store.decide_sufficiency(
        SESSION_ID,
        expected_revision=1,
        sufficient=False,
        idempotency_key="refresh",
    )

    mismatches = (
        {
            "session_id": "other-session",
            "expected_revision": 1,
            "action": "refresh",
            "request": {"sufficient": False},
        },
        {
            "session_id": SESSION_ID,
            "expected_revision": 2,
            "action": "refresh",
            "request": {"sufficient": False},
        },
        {
            "session_id": SESSION_ID,
            "expected_revision": 1,
            "action": "sufficient",
            "request": {"sufficient": False},
        },
        {
            "session_id": SESSION_ID,
            "expected_revision": 1,
            "action": "refresh",
            "request": {"sufficient": True},
        },
    )
    for mismatch in mismatches:
        with pytest.raises(ResearchIdempotencyConflict, match="idempotency"):
            store.get_decision_replay(
                mismatch["session_id"],
                expected_revision=mismatch["expected_revision"],
                action=mismatch["action"],
                request=mismatch["request"],
                idempotency_key="refresh",
            )


@pytest.mark.parametrize(
    "corruption",
    (
        "missing_request",
        "missing_result",
        "missing_claim",
        "missing_error_code",
        "wrong_claim_session",
        "invalid_retry_target",
        "non_retry_action",
    ),
)
def test_get_decision_replay_rejects_corrupt_null_result_envelopes(
    tmp_path: object,
    corruption: str,
) -> None:
    store = _store(tmp_path)
    _create(store)
    failed = store.record_failure(
        SESSION_ID,
        expected_revision=0,
        retry_target=ResearchState.ASSESSING,
        error_code="provider_timeout",
    )
    store.claim_retry(
        SESSION_ID,
        expected_revision=failed.revision,
        idempotency_key="retry-in-progress",
    )
    database = tmp_path / "research.sqlite3"  # type: ignore[operator]
    action = "retry"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM research_decisions WHERE idempotency_key = ?",
            ("retry-in-progress",),
        ).fetchone()
        assert row is not None
        envelope = json.loads(row[0])
        if corruption == "missing_request":
            del envelope["request"]
        elif corruption == "missing_result":
            del envelope["result"]
        elif corruption == "missing_claim":
            del envelope["claim"]
        elif corruption == "missing_error_code":
            del envelope["error_code"]
        elif corruption == "wrong_claim_session":
            envelope["claim"]["session_id"] = "01K4RESEARCH0000000000000001"
        elif corruption == "invalid_retry_target":
            envelope["retry_target"] = ResearchState.COMPLETED.value
        else:
            action = "refresh"
            connection.execute(
                "UPDATE research_decisions SET action = ? WHERE idempotency_key = ?",
                (action, "retry-in-progress"),
            )
        connection.execute(
            "UPDATE research_decisions SET payload_json = ? WHERE idempotency_key = ?",
            (json.dumps(envelope), "retry-in-progress"),
        )

    with pytest.raises(ValueError, match="stored decision result"):
        store.get_decision_replay(
            SESSION_ID,
            expected_revision=failed.revision,
            action=action,
            request={"expected_revision": failed.revision},
            idempotency_key="retry-in-progress",
        )


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
    reservation = store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID,),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="en",
        cookies_from_browser="firefox",
    )
    attempt = reservation.attempt
    replayed_reservation = store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID,),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="en",
        cookies_from_browser="firefox",
    )
    assert reservation.claimed is True
    assert replayed_reservation == type(reservation)(attempt, False)
    assert attempt.language == "en"
    assert attempt.cookies_from_browser == "firefox"
    with pytest.raises(ValueError, match="idempotency"):
        store.start_acquisition_attempt(
            SESSION_ID,
            expected_revision=4,
            video_ids=(VIDEO_ID,),
            idempotency_key="attempt-key",
            attempt_id="attempt-other",
            language="en",
            cookies_from_browser="firefox",
        )
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


def test_get_acquisition_replay_returns_only_the_indexed_exact_attempt(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    _prepare_acquisition_replay(store)
    attempt = store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID,),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="fr",
    ).attempt
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    replayed = store.get_acquisition_replay(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="attempt-key",
        language="fr",
        cookies_from_browser=None,
    )

    assert replayed == attempt
    assert (
        store.get_acquisition_replay(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="missing",
            language="fr",
            cookies_from_browser=None,
        )
        is None
    )


def test_get_acquisition_replay_rejects_a_reused_key_with_changed_payload(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    _prepare_acquisition_replay(store)
    store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID,),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="fr",
    )

    mismatches = (
        ("other-session", 4, "fr", None),
        (SESSION_ID, 5, "fr", None),
        (SESSION_ID, 4, "en", None),
        (SESSION_ID, 4, "fr", "firefox"),
    )
    for session_id, expected_revision, language, cookies_from_browser in mismatches:
        with pytest.raises(ValueError, match="idempotency"):
            store.get_acquisition_replay(
                session_id,
                expected_revision=expected_revision,
                idempotency_key="attempt-key",
                language=language,
                cookies_from_browser=cookies_from_browser,
            )


def test_failed_acquisition_is_reclaimed_only_by_durable_retry_and_keeps_progress(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    second_id = "def123GHI67"
    _create(store)
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=_assessment())
    store.decide_sufficiency(
        SESSION_ID,
        expected_revision=1,
        sufficient=False,
        idempotency_key="refresh",
    )
    store.record_candidates(
        SESSION_ID,
        expected_revision=2,
        candidates=(_candidate(), _candidate(video_id=second_id)),
        provider_name="provider",
        provider_version=1,
        errors=(),
    )
    store.approve_candidates(
        SESSION_ID,
        expected_revision=3,
        video_ids=(VIDEO_ID, second_id),
        idempotency_key="approve",
    )
    reservation = store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID, second_id),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="en",
        cookies_from_browser="firefox:research",
    )
    store.record_acquisition_progress(
        SESSION_ID,
        expected_revision=4,
        attempt_id=reservation.attempt.attempt_id,
        outcomes=(
            ResearchAcquisitionOutcome(
                "attempt-1",
                VIDEO_ID,
                CandidateStatus.ACQUIRED,
                None,
                "c" * 64,
            ),
        ),
    )
    failed = store.record_acquisition_failure(
        SESSION_ID,
        expected_revision=4,
        attempt_id="attempt-1",
        error_code="acquisition_unavailable",
    )

    plain_replay = store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID, second_id),
        idempotency_key="attempt-key",
        attempt_id="attempt-1",
        language="en",
        cookies_from_browser="firefox:research",
    )
    claimed_retry = store.claim_retry(
        SESSION_ID,
        expected_revision=failed.revision,
        idempotency_key="retry-key",
    )

    assert plain_replay.claimed is False
    assert plain_replay.attempt.status == "failed_retryable"
    assert claimed_retry.claimed is True
    assert claimed_retry.retry_target is ResearchState.ACQUIRING
    assert claimed_retry.acquisition_attempt is not None
    assert claimed_retry.acquisition_attempt.status == "running"
    assert claimed_retry.acquisition_attempt.video_ids == (VIDEO_ID, second_id)
    assert claimed_retry.acquisition_attempt.language == "en"
    assert claimed_retry.acquisition_attempt.cookies_from_browser == "firefox:research"
    assert store.get_session_history(SESSION_ID).acquisition_outcomes == (
        ResearchAcquisitionOutcome(
            "attempt-1",
            VIDEO_ID,
            CandidateStatus.ACQUIRED,
            None,
            "c" * 64,
        ),
    )

    store.record_acquisition_progress(
        SESSION_ID,
        expected_revision=claimed_retry.session.revision,
        attempt_id="attempt-1",
        outcomes=(
            ResearchAcquisitionOutcome(
                "attempt-1",
                second_id,
                CandidateStatus.ALREADY_PRESENT,
                None,
                "d" * 64,
            ),
        ),
    )
    completed = store.complete_acquisition_attempt(
        SESSION_ID,
        expected_revision=claimed_retry.session.revision,
        attempt_id="attempt-1",
    )
    store.complete_retry(
        "retry-key",
        result=completed,
        error_code=None,
    )
    replayed_retry = store.claim_retry(
        SESSION_ID,
        expected_revision=failed.revision,
        idempotency_key="retry-key",
    )

    assert replayed_retry.claimed is False
    assert replayed_retry.session == completed
    assert replayed_retry.error_code is None
    assert store.get_session(SESSION_ID).state is ResearchState.REINDEXING
    retry_decision = store.get_session_history(SESSION_ID).decisions[-1]
    assert json.loads(retry_decision.payload_json)["request"] == {
        "expected_revision": failed.revision
    }
    with pytest.raises(ValueError, match="idempotency"):
        store.claim_retry(
            SESSION_ID,
            expected_revision=completed.revision,
            idempotency_key="retry-key",
        )


def test_distinct_key_cannot_reserve_a_second_running_attempt(tmp_path: object) -> None:
    store = _store(tmp_path)
    _create(store)
    store.record_assessment(
        SESSION_ID,
        expected_revision=0,
        assessment=_assessment(),
    )
    store.decide_sufficiency(
        SESSION_ID,
        expected_revision=1,
        sufficient=False,
        idempotency_key="refresh",
    )
    store.record_candidates(
        SESSION_ID,
        expected_revision=2,
        candidates=(_candidate(),),
        provider_name="provider",
        provider_version=1,
        errors=(),
    )
    store.approve_candidates(
        SESSION_ID,
        expected_revision=3,
        video_ids=(VIDEO_ID,),
        idempotency_key="approve",
    )
    store.start_acquisition_attempt(
        SESSION_ID,
        expected_revision=4,
        video_ids=(VIDEO_ID,),
        idempotency_key="first-key",
        attempt_id="attempt-1",
    )

    with pytest.raises(ValueError, match="already running"):
        store.start_acquisition_attempt(
            SESSION_ID,
            expected_revision=4,
            video_ids=(VIDEO_ID,),
            idempotency_key="second-key",
            attempt_id="attempt-2",
        )

    attempts = store.get_session_history(SESSION_ID).acquisition_attempts
    assert tuple(attempt.attempt_id for attempt in attempts) == ("attempt-1",)


def test_attempt_execution_lock_is_exclusive_and_released_automatically(
    tmp_path: object,
) -> None:
    store = _store(tmp_path)
    contender = ResearchStore(tmp_path / "research.sqlite3")  # type: ignore[operator]

    with (
        store.acquisition_execution_lock("attempt-1") as first_claimed,
        contender.acquisition_execution_lock("attempt-1") as second_claimed,
    ):
        assert first_claimed is True
        assert second_claimed is False

    with contender.acquisition_execution_lock("attempt-1") as recovered:
        assert recovered is True


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
