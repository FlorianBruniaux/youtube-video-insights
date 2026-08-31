from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime

import pytest

from yt_insights.paths import DataPaths
from yt_insights.research.acquisition import CandidateAcquisitionOutcome
from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.discovery import DiscoveryResult
from yt_insights.research.models import (
    CandidateStatus,
    DatabaseSnapshot,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    ResearchCandidate,
    ResearchState,
    VideoEvidence,
)
from yt_insights.research.store import ResearchStore
from yt_insights.research.workflow import ResearchResponse, ResearchWorkflow

NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)
SESSION_ID = "01K4RESEARCH0000000000000000"
VIDEO_ID = "abc123DEF45"


class FakeEvidenceReader:
    def __init__(self, *, fails: bool = False) -> None:
        self.fails = fails
        self.passage_calls: list[tuple[str, tuple[str, ...], int]] = []

    def capture_snapshot(self) -> DatabaseSnapshot:
        if self.fails:
            raise AssessmentRetryableError("private local index problem")
        return DatabaseSnapshot("search-generation", "catalog-generation")

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None:
        if self.fails:
            raise AssessmentRetryableError("private local index problem")

    def search_passages(
        self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
    ) -> tuple[PassageEvidence, ...]:
        self.passage_calls.append((query.text, languages, limit))
        return (
            PassageEvidence(
                query=query.text,
                passage_id="passage-1",
                video_id=VIDEO_ID,
                channel_id="UC12345678901234567890AB",
                rank=1,
                url=f"https://youtube.com/watch?v={VIDEO_ID}&t=12s",
                excerpt="Bounded local passage.",
                source_sha256="a" * 64,
            ),
        )

    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]:
        return (
            VideoEvidence(
                query=query.text,
                video_id=VIDEO_ID,
                source_keys=("local",),
                title="Local evidence",
                published_at=None,
                rank=1,
                watch_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
            ),
        )


class GuardStore:
    def create_session(self, **kwargs: object) -> object:
        raise AssertionError("store must not open for invalid input")


class FakeDiscoveryProvider:
    def __init__(
        self, result: DiscoveryResult | Exception
    ) -> None:
        self.result = result
        self.calls: list[tuple[tuple[QuerySpec, ...], int]] = []

    def discover(
        self, queries: tuple[QuerySpec, ...], *, limit: int = 10
    ) -> DiscoveryResult:
        self.calls.append((queries, limit))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _candidate(*, video_id: str = "zyx987WVUT0") -> ResearchCandidate:
    return ResearchCandidate(
        video_id=video_id,
        title="Candidate title",
        channel_id="UC12345678901234567890AB",
        channel_title="Candidate channel",
        published_at=date(2026, 8, 30),
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
        matched_queries=("Local query",),
        original_rank=1,
        status=CandidateStatus.CANDIDATE,
    )


def _workflow(
    tmp_path,
    reader: FakeEvidenceReader,
    provider: FakeDiscoveryProvider | None = None,
    *,
    acquisition_service: object | None = None,
    index_refresher: object | None = None,
) -> ResearchWorkflow:
    kwargs: dict[str, object] = {}
    if acquisition_service is not None:
        kwargs["acquisition_service"] = acquisition_service
    if index_refresher is not None:
        kwargs["index_refresher"] = index_refresher
    return ResearchWorkflow(
        store=ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW),
        evidence_reader=reader,
        discovery_provider=provider,
        data_paths=DataPaths.from_root(tmp_path / "data"),
        now=lambda: NOW,
        session_id_factory=lambda: SESSION_ID,
        **kwargs,
    )


class FakeAcquisitionService:
    def __init__(
        self,
        outcomes: tuple[CandidateAcquisitionOutcome, ...],
        *,
        before_network: object | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.before_network = before_network
        self.calls: list[tuple[tuple[ResearchCandidate, ...], str, str | None]] = []

    def acquire_approved(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        data_paths: DataPaths,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> tuple[CandidateAcquisitionOutcome, ...]:
        if callable(self.before_network):
            self.before_network()
        self.calls.append((candidates, language, cookies_from_browser))
        return self.outcomes


def _prepare_approved_session(
    workflow: ResearchWorkflow,
    provider: FakeDiscoveryProvider,
    *,
    approved_ids: tuple[str, ...],
) -> None:
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=("fr",),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )
    workflow.discover(SESSION_ID, expected_revision=2)
    workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=approved_ids,
        idempotency_key="approve-key",
    )


def test_start_validates_before_opening_store_or_evidence() -> None:
    workflow = ResearchWorkflow(
        store=GuardStore(),
        evidence_reader=object(),
        now=lambda: NOW,
        session_id_factory=lambda: SESSION_ID,
    )

    with pytest.raises(ValueError, match="must not be empty"):
        workflow.start(
            topic="   ",
            queries=("untrusted query",),
            languages=(),
            freshness_profile=FreshnessProfile.FAST,
        )


def test_start_persists_a_local_assessment_with_exact_fingerprint_and_languages(tmp_path) -> None:
    reader = FakeEvidenceReader()
    workflow = _workflow(tmp_path, reader)

    response = workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=("fr", "en"),
        freshness_profile=FreshnessProfile.FAST,
    )

    payload = response.to_dict()
    assert payload["session"]["revision"] == 1
    assert payload["session"]["state"] == "awaiting_sufficiency_confirmation"
    assert payload["session"]["topic"] == "Local evidence"
    assert payload["required_user_action"] == "confirm_sufficiency_or_refresh"
    assert payload["assessment"]["freshness"]["reason"] == "never_checked"
    assert reader.passage_calls == [("Local query", ("fr", "en"), 20)]
    assert workflow._store.last_successful_discovery_at(  # type: ignore[attr-defined]
        payload["session"]["discovery_fingerprint"]
    ) is None


def test_start_records_a_retryable_local_index_failure_without_an_assessment(tmp_path) -> None:
    workflow = _workflow(tmp_path, FakeEvidenceReader(fails=True))

    response = workflow.start(
        topic="Local evidence",
        queries=("private failing query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )

    payload = response.to_dict()
    assert payload["session"]["state"] == "failed_retryable"
    assert payload["session"]["revision"] == 1
    assert payload["assessment"] is None
    assert payload["error_code"] == "local_index_unavailable"


def test_decide_refresh_persists_discovering_without_calling_the_network_provider(tmp_path) -> None:
    workflow = _workflow(tmp_path, FakeEvidenceReader())
    started = workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )

    response = workflow.decide(
        SESSION_ID,
        expected_revision=started.to_dict()["session"]["revision"],
        decision="refresh",
        idempotency_key="refresh-key",
    )

    assert response.to_dict()["session"]["state"] == "discovering"
    assert response.to_dict()["error_code"] is None


def test_discover_persists_exact_snapshot_only_after_explicit_refresh(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    corpus_path = tmp_path / "corpus.vtt"
    catalog_path = tmp_path / "catalog.sqlite3"
    index_path = tmp_path / "search.sqlite3"
    for path in (corpus_path, catalog_path, index_path):
        path.write_bytes(b"immutable input")
    original_hashes = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (corpus_path, catalog_path, index_path)
    }
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )

    authorized = workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )
    discovered = workflow.discover(SESSION_ID, expected_revision=2)

    assert authorized.to_dict()["session"]["state"] == "discovering"
    assert provider.calls == [((QuerySpec("Local query"),), 10)]
    assert discovered.to_dict()["session"]["state"] == "awaiting_candidate_approval"
    assert discovered.to_dict()["session"]["revision"] == 3
    assert discovered.candidates == (candidate,)
    assert {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (corpus_path, catalog_path, index_path)
    } == original_hashes


def test_discover_records_retryable_failure_without_provider_details(tmp_path) -> None:
    provider = FakeDiscoveryProvider(RuntimeError("private provider failure"))
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )

    response = workflow.discover(SESSION_ID, expected_revision=2)

    assert response.to_dict()["session"]["state"] == "failed_retryable"
    assert response.to_dict()["session"]["retry_target"] == "discovering"
    assert response.to_dict()["error_code"] == "discovery_unavailable"
    assert "private provider failure" not in json.dumps(response.to_dict())


def test_discover_bounds_unexpected_provider_exceptions_and_persists_failure(
    tmp_path,
) -> None:
    provider = FakeDiscoveryProvider(LookupError("private lookup failure"))
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )

    response = workflow.discover(SESSION_ID, expected_revision=2)

    assert response.error_code == "discovery_unavailable"
    assert response.session.state is ResearchState.FAILED_RETRYABLE
    assert response.session.retry_target is ResearchState.DISCOVERING
    assert "private lookup failure" not in json.dumps(response.to_dict())


def test_discover_keeps_partial_candidates_reviewable(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), ("partial_metadata",), False)
    )
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )

    response = workflow.discover(SESSION_ID, expected_revision=2)

    assert response.to_dict()["session"]["state"] == "awaiting_candidate_approval"
    assert response.candidates == (candidate,)
    assert json.loads(workflow._store.get_session_history(SESSION_ID).events[-1].payload_json) == {  # type: ignore[attr-defined]
        "errors": ["partial_metadata"],
        "provider_name": "yt-dlp",
        "provider_version": 1,
    }


def test_cancel_rejects_the_sufficiency_confirmation_state(tmp_path) -> None:
    workflow = _workflow(tmp_path, FakeEvidenceReader())
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )

    with pytest.raises(ValueError, match="awaiting candidate approval"):
        workflow.cancel(
            SESSION_ID,
            expected_revision=1,
            idempotency_key="cancel-sufficiency",
        )


def test_cancel_replays_only_the_original_candidate_decision(tmp_path) -> None:
    candidate = _candidate()
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        FakeDiscoveryProvider(DiscoveryResult("yt-dlp", 1, (candidate,), (), True)),
    )
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )
    workflow.discover(SESSION_ID, expected_revision=2)

    cancelled = workflow.cancel(
        SESSION_ID,
        expected_revision=3,
        idempotency_key="cancel-key",
    )
    replayed = workflow.cancel(
        SESSION_ID,
        expected_revision=3,
        idempotency_key="cancel-key",
    )

    assert cancelled.to_dict()["session"]["state"] == "cancelled"
    assert replayed.to_dict()["session"] == cancelled.to_dict()["session"]
    with pytest.raises(ValueError, match="idempotency"):
        workflow.cancel(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="cancel-key",
        )
    with pytest.raises(ValueError, match="transition"):
        workflow.cancel(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="different-cancel-key",
        )


def test_candidates_approve_and_cancel_use_current_snapshot_and_revision(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=(),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )
    workflow.discover(SESSION_ID, expected_revision=2)

    listed = workflow.candidates(SESSION_ID)
    approved = workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=(candidate.video_id,),
        idempotency_key="approve-key",
    )

    assert listed.candidates == (candidate,)
    assert approved.to_dict()["session"]["state"] == "acquiring"
    with pytest.raises(ValueError, match="awaiting candidate approval"):
        workflow.cancel(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="cancel-active",
        )


def test_acquire_reserves_before_network_and_reassesses_only_the_approved_ids(
    tmp_path,
) -> None:
    first = _candidate(video_id="zyx987WVUT0")
    second = _candidate(video_id="zyx987WVUT1")
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (first, second), (), True)
    )
    refresh_calls: list[DataPaths] = []
    workflow: ResearchWorkflow

    def assert_attempt_is_durable() -> None:
        history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
        assert len(history.acquisition_attempts) == 1
        assert history.acquisition_attempts[0].status == "running"
        assert history.acquisition_outcomes == ()

    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                first.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        ),
        before_network=assert_attempt_is_durable,
    )
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(first.video_id,),
    )

    response = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
        cookies_from_browser="firefox",
    )

    assert [candidate.video_id for candidate in acquisition.calls[0][0]] == [first.video_id]
    assert acquisition.calls[0][1:] == ("fr", "firefox")
    assert len(refresh_calls) == 1
    assert response.session.state.value == "awaiting_sufficiency_confirmation"
    assert response.session.revision == 7
    assert response.required_user_action == "confirm_sufficiency_or_refresh"
    history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
    assert history.acquisition_attempts[0].status == "completed"
    assert history.acquisition_outcomes[0].video_id == first.video_id


def test_acquire_rejects_stale_revision_before_attempt_or_network(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: None,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )

    with pytest.raises(ValueError, match="revision is stale"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=3,
            idempotency_key="stale-acquire-key",
            language="fr",
        )

    assert acquisition.calls == []
    assert workflow._store.get_session_history(SESSION_ID).acquisition_attempts == ()  # type: ignore[attr-defined]


def test_acquire_replay_returns_committed_result_without_redownload(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ALREADY_PRESENT,
                None,
                "b" * 64,
            ),
        )
    )
    refresh_calls: list[DataPaths] = []
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    completed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )

    replayed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )

    assert replayed.to_dict() == completed.to_dict()
    assert len(acquisition.calls) == 1
    assert len(refresh_calls) == 1
    with pytest.raises(ValueError, match="payload differs"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="acquire-key",
            language="en",
        )
    assert len(acquisition.calls) == 1


def test_concurrent_same_key_acquire_replay_never_claims_network_twice(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    replayed: list[ResearchResponse] = []
    entered = False
    workflow: ResearchWorkflow

    def replay_while_first_call_owns_attempt() -> None:
        nonlocal entered
        if entered:
            return
        entered = True
        replayed.append(
            workflow.acquire(
                SESSION_ID,
                expected_revision=4,
                idempotency_key="shared-acquire-key",
                language="fr",
            )
        )

    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        ),
        before_network=replay_while_first_call_owns_attempt,
    )
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: None,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )

    completed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="shared-acquire-key",
        language="fr",
    )

    assert len(acquisition.calls) == 1
    assert len(replayed) == 1
    assert replayed[0].session.state is ResearchState.ACQUIRING
    assert replayed[0].error_code == "acquisition_in_progress"
    assert completed.session.state is ResearchState.AWAITING_SUFFICIENCY


def test_acquire_key_conflict_is_rejected_before_network(tmp_path) -> None:
    candidate = _candidate()
    other = _candidate(video_id="zyx987WVUT1")
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate, other), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: None,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    workflow._store.start_acquisition_attempt(  # type: ignore[attr-defined]
        SESSION_ID,
        expected_revision=4,
        video_ids=(other.video_id,),
        idempotency_key="conflicting-key",
        attempt_id="preexisting-attempt",
    )

    with pytest.raises(ValueError, match="payload differs"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="conflicting-key",
            language="fr",
        )

    assert acquisition.calls == []


def test_reindex_failure_is_retryable_without_redownloading(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    refresh_attempts = 0

    def flaky_refresh(paths: DataPaths) -> None:
        nonlocal refresh_attempts
        refresh_attempts += 1
        if refresh_attempts == 1:
            raise RuntimeError("private index failure")

    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=flaky_refresh,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )

    failed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )
    resumed = workflow.retry(
        SESSION_ID,
        expected_revision=6,
        idempotency_key="retry-key",
    )

    assert failed.session.state.value == "failed_retryable"
    assert failed.session.retry_target.value == "reindexing"
    assert failed.error_code == "index_refresh_failed"
    assert resumed.session.state.value == "awaiting_sufficiency_confirmation"
    assert resumed.required_user_action == "confirm_sufficiency_or_refresh"
    assert len(acquisition.calls) == 1
    assert refresh_attempts == 2


def test_acquisition_retry_reuses_persisted_inputs_when_batch_has_no_progress(
    tmp_path,
) -> None:
    first = _candidate(video_id="zyx987WVUT0")
    second = _candidate(video_id="zyx987WVUT1")
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (first, second), (), True)
    )

    class NoProgressThenSuccessfulAcquisition:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], str, str | None]] = []
            self.failed_once: set[str] = set()

        def acquire_approved(
            self,
            candidates: tuple[ResearchCandidate, ...],
            *,
            data_paths: DataPaths,
            language: str,
            cookies_from_browser: str | None = None,
        ) -> tuple[CandidateAcquisitionOutcome, ...]:
            video_ids = tuple(candidate.video_id for candidate in candidates)
            self.calls.append((video_ids, language, cookies_from_browser))
            assert len(video_ids) == 1
            if video_ids[0] not in self.failed_once:
                self.failed_once.add(video_ids[0])
                raise LookupError("private downloader failure")
            return (
                CandidateAcquisitionOutcome(
                    video_ids[0],
                    CandidateStatus.ACQUIRED,
                    None,
                    ("b" if video_ids[0] == first.video_id else "c") * 64,
                ),
            )

    acquisition = NoProgressThenSuccessfulAcquisition()
    refresh_calls: list[DataPaths] = []
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(first.video_id, second.video_id),
    )

    failed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="en",
        cookies_from_browser="firefox:research",
    )
    calls_after_failure = tuple(acquisition.calls)
    plain_replay = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="en",
        cookies_from_browser="firefox:research",
    )
    retried = workflow.retry(
        SESSION_ID,
        expected_revision=failed.session.revision,
        idempotency_key="retry-key",
    )

    assert failed.session.state is ResearchState.FAILED_RETRYABLE
    assert failed.session.retry_target is ResearchState.ACQUIRING
    assert plain_replay.to_dict() == failed.to_dict()
    assert tuple(acquisition.calls[:2]) == calls_after_failure
    assert acquisition.calls == [
        ((first.video_id,), "en", "firefox:research"),
        ((second.video_id,), "en", "firefox:research"),
        ((first.video_id,), "en", "firefox:research"),
        ((second.video_id,), "en", "firefox:research"),
    ]
    assert retried.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert len(refresh_calls) == 1
    history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
    assert [(outcome.video_id, outcome.source_sha256) for outcome in history.acquisition_outcomes] == [
        (first.video_id, "b" * 64),
        (second.video_id, "c" * 64),
    ]
    assert history.acquisition_attempts[0].language == "en"
    assert history.acquisition_attempts[0].cookies_from_browser == "firefox:research"

    replayed_retry = workflow.retry(
        SESSION_ID,
        expected_revision=failed.session.revision,
        idempotency_key="retry-key",
    )
    assert replayed_retry.to_dict() == retried.to_dict()
    assert len(acquisition.calls) == 4
    with pytest.raises(ValueError, match="idempotency"):
        workflow.retry(
            SESSION_ID,
            expected_revision=retried.session.revision,
            idempotency_key="retry-key",
        )


def test_mixed_acquisition_persists_every_outcome_and_reindexes_once(tmp_path) -> None:
    first = _candidate(video_id="zyx987WVUT0")
    second = _candidate(video_id="zyx987WVUT1")
    third = _candidate(video_id="zyx987WVUT2")
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (first, second, third), (), True)
    )

    class MixedAcquisition:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def acquire_approved(
            self,
            candidates: tuple[ResearchCandidate, ...],
            *,
            data_paths: DataPaths,
            language: str,
            cookies_from_browser: str | None = None,
        ) -> tuple[CandidateAcquisitionOutcome, ...]:
            video_id = candidates[0].video_id
            self.calls.append(video_id)
            if video_id == second.video_id:
                raise LookupError("private second-video failure")
            status = (
                CandidateStatus.ACQUIRED
                if video_id == first.video_id
                else CandidateStatus.ALREADY_PRESENT
            )
            return (
                CandidateAcquisitionOutcome(
                    video_id,
                    status,
                    None,
                    ("b" if video_id == first.video_id else "d") * 64,
                ),
            )

    acquisition = MixedAcquisition()
    refresh_calls: list[DataPaths] = []
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(first.video_id, second.video_id, third.video_id),
    )

    response = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="mixed-acquire-key",
        language="en",
    )

    assert acquisition.calls == [first.video_id, second.video_id, third.video_id]
    assert len(refresh_calls) == 1
    assert response.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert response.required_user_action == "confirm_sufficiency_or_refresh"
    assert response.error_code is None
    history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
    assert history.acquisition_attempts[0].status == "completed"
    assert [outcome.status for outcome in history.acquisition_outcomes] == [
        CandidateStatus.ACQUIRED,
        CandidateStatus.FAILED_RETRYABLE,
        CandidateStatus.ALREADY_PRESENT,
    ]


def test_unexpected_index_database_error_is_bounded_and_retryable(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )

    def fail_refresh(paths: DataPaths) -> None:
        raise sqlite3.DatabaseError("private database detail")

    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=fail_refresh,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )

    response = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )

    assert response.session.state is ResearchState.FAILED_RETRYABLE
    assert response.session.retry_target is ResearchState.REINDEXING
    assert response.error_code == "index_refresh_failed"
    assert "private database detail" not in json.dumps(response.to_dict())


def test_released_attempt_lock_recovers_a_crashed_acquisition_without_duplicates(
    tmp_path,
) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )

    class CrashOnceAcquisition:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], str, str | None]] = []

        def acquire_approved(
            self,
            candidates: tuple[ResearchCandidate, ...],
            *,
            data_paths: DataPaths,
            language: str,
            cookies_from_browser: str | None = None,
        ) -> tuple[CandidateAcquisitionOutcome, ...]:
            video_ids = tuple(candidate.video_id for candidate in candidates)
            self.calls.append((video_ids, language, cookies_from_browser))
            if len(self.calls) == 1:
                raise SystemExit("simulated process crash")
            return (
                CandidateAcquisitionOutcome(
                    video_ids[0],
                    CandidateStatus.ACQUIRED,
                    None,
                    "b" * 64,
                ),
            )

    acquisition = CrashOnceAcquisition()
    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: None,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )

    with pytest.raises(SystemExit, match="simulated process crash"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="acquire-key",
            language="en",
            cookies_from_browser="firefox:research",
        )

    history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
    attempt = history.acquisition_attempts[0]
    assert attempt.status == "running"
    assert history.acquisition_outcomes == ()

    with workflow._store.acquisition_execution_lock(attempt.attempt_id) as claimed:  # type: ignore[attr-defined]
        assert claimed is True
        acquire_replay = workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="acquire-key",
            language="en",
            cookies_from_browser="firefox:research",
        )
        retry_while_active = workflow.retry(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="retry-key",
        )

    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="retry-key",
    )

    assert acquire_replay.error_code == "acquisition_in_progress"
    assert retry_while_active.error_code == "acquisition_in_progress"
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert acquisition.calls == [
        ((candidate.video_id,), "en", "firefox:research"),
        ((candidate.video_id,), "en", "firefox:research"),
    ]


def test_reindex_retry_lock_replays_live_and_recovers_after_crash(tmp_path) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    retry_replays: list[ResearchResponse] = []
    refresh_calls = 0
    workflow: ResearchWorkflow
    failed_revision = -1

    def crashing_refresh(paths: DataPaths) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise RuntimeError("bounded first refresh failure")
        if refresh_calls == 2:
            retry_replays.append(
                workflow.retry(
                    SESSION_ID,
                    expected_revision=failed_revision,
                    idempotency_key="retry-key",
                )
            )
            raise SystemExit("simulated reindex crash")

    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=crashing_refresh,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    failed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )
    failed_revision = failed.session.revision

    with pytest.raises(SystemExit, match="simulated reindex crash"):
        workflow.retry(
            SESSION_ID,
            expected_revision=failed_revision,
            idempotency_key="retry-key",
        )
    crashed = workflow.status(SESSION_ID).session
    with pytest.raises(ValueError):
        workflow.retry(
            SESSION_ID,
            expected_revision=crashed.revision,
            idempotency_key="new-key",
        )
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=failed_revision,
        idempotency_key="retry-key",
    )

    assert retry_replays[0].error_code == "retry_in_progress"
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert refresh_calls == 3
    assert len(acquisition.calls) == 1


def test_assessment_retry_lock_replays_live_and_recovers_after_crash(tmp_path) -> None:
    retry_replays: list[ResearchResponse] = []
    workflow: ResearchWorkflow
    failed_revision = -1

    class CrashAssessmentReader(FakeEvidenceReader):
        def __init__(self) -> None:
            super().__init__()
            self.phase = "initial_failure"

        def capture_snapshot(self) -> DatabaseSnapshot:
            if self.phase == "initial_failure":
                raise AssessmentRetryableError("bounded initial assessment failure")
            if self.phase == "crash":
                retry_replays.append(
                    workflow.retry(
                        SESSION_ID,
                        expected_revision=failed_revision,
                        idempotency_key="retry-key",
                    )
                )
                self.phase = "ready"
                raise SystemExit("simulated assessment crash")
            return super().capture_snapshot()

    reader = CrashAssessmentReader()
    workflow = _workflow(tmp_path, reader)
    failed = workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=("fr",),
        freshness_profile=FreshnessProfile.FAST,
    )
    failed_revision = failed.session.revision
    reader.phase = "crash"

    with pytest.raises(SystemExit, match="simulated assessment crash"):
        workflow.retry(
            SESSION_ID,
            expected_revision=failed_revision,
            idempotency_key="retry-key",
        )
    crashed = workflow.status(SESSION_ID).session
    with pytest.raises(ValueError):
        workflow.retry(
            SESSION_ID,
            expected_revision=crashed.revision,
            idempotency_key="new-key",
        )
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=failed_revision,
        idempotency_key="retry-key",
    )

    assert retry_replays[0].error_code == "retry_in_progress"
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY


def test_discovery_retry_lock_replays_live_and_recovers_after_crash(tmp_path) -> None:
    candidate = _candidate()
    retry_replays: list[ResearchResponse] = []
    workflow: ResearchWorkflow
    failed_revision = -1

    class CrashDiscoveryProvider(FakeDiscoveryProvider):
        def __init__(self) -> None:
            super().__init__(
                DiscoveryResult("yt-dlp", 1, (candidate,), (), True)  # type: ignore[arg-type]
            )
            self.phase = "initial_failure"

        def discover(
            self, queries: tuple[QuerySpec, ...], *, limit: int = 10
        ) -> DiscoveryResult:
            if self.phase == "initial_failure":
                raise RuntimeError("bounded initial discovery failure")
            if self.phase == "crash":
                retry_replays.append(
                    workflow.retry(
                        SESSION_ID,
                        expected_revision=failed_revision,
                        idempotency_key="retry-key",
                    )
                )
                self.phase = "ready"
                raise SystemExit("simulated discovery crash")
            return DiscoveryResult("yt-dlp", 1, (candidate,), (), True)

    provider = CrashDiscoveryProvider()
    workflow = _workflow(tmp_path, FakeEvidenceReader(), provider)
    workflow.start(
        topic="Local evidence",
        queries=("Local query",),
        languages=("fr",),
        freshness_profile=FreshnessProfile.FAST,
    )
    workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-key",
    )
    failed = workflow.discover(SESSION_ID, expected_revision=2)
    failed_revision = failed.session.revision
    provider.phase = "crash"

    with pytest.raises(SystemExit, match="simulated discovery crash"):
        workflow.retry(
            SESSION_ID,
            expected_revision=failed_revision,
            idempotency_key="retry-key",
        )
    crashed = workflow.status(SESSION_ID).session
    with pytest.raises(ValueError):
        workflow.retry(
            SESSION_ID,
            expected_revision=crashed.revision,
            idempotency_key="new-key",
        )
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=failed_revision,
        idempotency_key="retry-key",
    )

    assert retry_replays[0].error_code == "retry_in_progress"
    assert recovered.session.state is ResearchState.AWAITING_CANDIDATES


def test_acquisition_retry_resumes_reindex_substage_without_redownload(
    tmp_path,
) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )

    class FailThenAcquire:
        def __init__(self) -> None:
            self.calls = 0

        def acquire_approved(
            self,
            candidates: tuple[ResearchCandidate, ...],
            *,
            data_paths: DataPaths,
            language: str,
            cookies_from_browser: str | None = None,
        ) -> tuple[CandidateAcquisitionOutcome, ...]:
            self.calls += 1
            if self.calls == 1:
                raise LookupError("bounded first acquisition failure")
            return (
                CandidateAcquisitionOutcome(
                    candidate.video_id,
                    CandidateStatus.ACQUIRED,
                    None,
                    "b" * 64,
                ),
            )

    acquisition = FailThenAcquire()
    refresh_calls = 0

    def crash_once(paths: DataPaths) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise SystemExit("simulated refresh crash after retry download")

    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=crash_once,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    failed = workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquire-key",
        language="fr",
    )

    with pytest.raises(SystemExit, match="refresh crash after retry download"):
        workflow.retry(
            SESSION_ID,
            expected_revision=failed.session.revision,
            idempotency_key="retry-key",
        )
    crashed = workflow.status(SESSION_ID)
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=failed.session.revision,
        idempotency_key="retry-key",
    )

    assert crashed.session.state is ResearchState.REINDEXING
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert acquisition.calls == 2
    assert refresh_calls == 2
    history = workflow._store.get_session_history(SESSION_ID)  # type: ignore[attr-defined]
    assert history.acquisition_attempts[0].status == "completed"


def test_initial_acquire_reindex_crash_retries_completed_attempt_without_download(
    tmp_path,
) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    refresh_calls = 0

    def crash_once(paths: DataPaths) -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise SystemExit("simulated initial refresh crash")

    workflow = _workflow(
        tmp_path,
        FakeEvidenceReader(),
        provider,
        acquisition_service=acquisition,
        index_refresher=crash_once,
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    with pytest.raises(SystemExit, match="simulated initial refresh crash"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="acquire-key",
            language="fr",
        )
    crashed = workflow.status(SESSION_ID)
    attempt = workflow._store.get_session_history(SESSION_ID).acquisition_attempts[0]  # type: ignore[attr-defined]

    with workflow._store.acquisition_execution_lock(attempt.attempt_id) as claimed:  # type: ignore[attr-defined]
        assert claimed is True
        blocked = workflow.retry(
            SESSION_ID,
            expected_revision=crashed.session.revision,
            idempotency_key="retry-key",
        )
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=crashed.session.revision,
        idempotency_key="retry-key",
    )

    assert crashed.session.state is ResearchState.REINDEXING
    assert attempt.status == "completed"
    assert blocked.error_code == "acquisition_in_progress"
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert len(acquisition.calls) == 1
    assert refresh_calls == 2


def test_initial_acquire_assessment_crash_retries_without_download_or_reindex(
    tmp_path,
) -> None:
    candidate = _candidate()
    provider = FakeDiscoveryProvider(
        DiscoveryResult("yt-dlp", 1, (candidate,), (), True)
    )
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                candidate.video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )

    class CrashOnceAssessment(FakeEvidenceReader):
        def __init__(self) -> None:
            super().__init__()
            self.crash = False

        def capture_snapshot(self) -> DatabaseSnapshot:
            if self.crash:
                self.crash = False
                raise SystemExit("simulated initial assessment crash")
            return super().capture_snapshot()

    reader = CrashOnceAssessment()
    refresh_calls: list[DataPaths] = []
    workflow = _workflow(
        tmp_path,
        reader,
        provider,
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    _prepare_approved_session(
        workflow,
        provider,
        approved_ids=(candidate.video_id,),
    )
    reader.crash = True
    with pytest.raises(SystemExit, match="simulated initial assessment crash"):
        workflow.acquire(
            SESSION_ID,
            expected_revision=4,
            idempotency_key="acquire-key",
            language="fr",
        )
    crashed = workflow.status(SESSION_ID)
    recovered = workflow.retry(
        SESSION_ID,
        expected_revision=crashed.session.revision,
        idempotency_key="retry-key",
    )

    assert crashed.session.state is ResearchState.ASSESSING
    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert len(acquisition.calls) == 1
    assert len(refresh_calls) == 1
