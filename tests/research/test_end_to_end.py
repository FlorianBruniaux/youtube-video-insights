from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from yt_insights.paths import DataPaths
from yt_insights.research.acquisition import CandidateAcquisitionOutcome
from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.discovery import DiscoveryResult
from yt_insights.research.dossier import (
    DossierExportRequest,
    DossierRootConstraint,
)
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

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
SESSION_ID = "01K4RESEARCH0000000000000000"
LOCAL_VIDEO_ID = "abc123DEF45"
ACQUIRED_VIDEO_ID = "newVID00001"
NO_TRANSCRIPT_VIDEO_ID = "newVID00002"
QUERY = "AI product engineering team workflows"
LOCAL_URL = f"https://www.youtube.com/watch?v={LOCAL_VIDEO_ID}&t=12s"
ACQUIRED_URL = f"https://www.youtube.com/watch?v={ACQUIRED_VIDEO_ID}&t=45s"


class MutableEvidenceReader:
    def __init__(self, *, empty: bool = False, stale_snapshot: bool = False) -> None:
        self.empty = empty
        self.stale_snapshot = stale_snapshot
        self.generation = 1
        self.passage_calls = 0
        self.video_calls = 0

    def capture_snapshot(self) -> DatabaseSnapshot:
        return DatabaseSnapshot(
            f"search-generation-{self.generation}",
            f"catalog-generation-{self.generation}",
        )

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None:
        if self.stale_snapshot or snapshot != self.capture_snapshot():
            raise AssessmentRetryableError("local evidence changed during assessment")

    def search_passages(
        self,
        query: QuerySpec,
        *,
        languages: tuple[str, ...],
        limit: int,
    ) -> tuple[PassageEvidence, ...]:
        self.passage_calls += 1
        assert query == QuerySpec(QUERY)
        assert languages == ("en",)
        assert limit == 20
        if self.empty:
            return ()
        passages = [
            PassageEvidence(
                query=query.text,
                passage_id="passage-local-1",
                video_id=LOCAL_VIDEO_ID,
                channel_id="UCLOCALCHANNEL0000000001",
                rank=1,
                url=LOCAL_URL,
                excerpt="Existing teams use bounded AI review workflows.",
                source_sha256="a" * 64,
            )
        ]
        if self.generation > 1:
            passages.append(
                PassageEvidence(
                    query=query.text,
                    passage_id="passage-acquired-1",
                    video_id=ACQUIRED_VIDEO_ID,
                    channel_id="UCNEWCHANNEL00000000003",
                    rank=2,
                    url=ACQUIRED_URL,
                    excerpt="A newly acquired source adds current team evidence.",
                    source_sha256="c" * 64,
                )
            )
        return tuple(passages)

    def search_videos(
        self, query: QuerySpec, *, limit: int
    ) -> tuple[VideoEvidence, ...]:
        self.video_calls += 1
        assert query == QuerySpec(QUERY)
        assert limit == 20
        if self.empty:
            return ()
        videos = [
            VideoEvidence(
                query=query.text,
                video_id=LOCAL_VIDEO_ID,
                source_keys=("local-corpus",),
                title="Existing team workflow",
                published_at=date(2026, 7, 1),
                rank=1,
                watch_url=f"https://www.youtube.com/watch?v={LOCAL_VIDEO_ID}",
            )
        ]
        if self.generation > 1:
            videos.append(
                VideoEvidence(
                    query=query.text,
                    video_id=ACQUIRED_VIDEO_ID,
                    source_keys=("approved-refresh",),
                    title="New team workflow",
                    published_at=date(2026, 8, 30),
                    rank=2,
                    watch_url=(
                        f"https://www.youtube.com/watch?v={ACQUIRED_VIDEO_ID}"
                    ),
                )
            )
        return tuple(videos)


class StaticDiscoveryProvider:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[tuple[QuerySpec, ...], int]] = []

    def discover(
        self, queries: tuple[QuerySpec, ...], *, limit: int = 10
    ) -> DiscoveryResult:
        self.calls.append((queries, limit))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result  # type: ignore[return-value]


class MixedAcquisitionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    def acquire_approved(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        data_paths: DataPaths,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> tuple[CandidateAcquisitionOutcome, ...]:
        assert len(candidates) == 1
        video_id = candidates[0].video_id
        self.calls.append((video_id, language, cookies_from_browser))
        if video_id == ACQUIRED_VIDEO_ID:
            return (
                CandidateAcquisitionOutcome(
                    video_id,
                    CandidateStatus.ACQUIRED,
                    None,
                    "c" * 64,
                ),
            )
        if video_id == NO_TRANSCRIPT_VIDEO_ID:
            return (
                CandidateAcquisitionOutcome(
                    video_id,
                    CandidateStatus.NO_TRANSCRIPT,
                    "transcript_unavailable",
                    None,
                ),
            )
        raise AssertionError("workflow acquired an unapproved candidate")


class RefreshController:
    def __init__(self, reader: MutableEvidenceReader) -> None:
        self.reader = reader
        self.fail = False
        self.calls: list[DataPaths] = []

    def __call__(self, data_paths: DataPaths) -> None:
        self.calls.append(data_paths)
        if self.fail:
            raise RuntimeError("private index publication failure")
        self.reader.generation += 1


@dataclass(slots=True)
class Harness:
    workflow: ResearchWorkflow
    store: ResearchStore
    reader: MutableEvidenceReader
    provider: StaticDiscoveryProvider
    acquisition: MixedAcquisitionService
    refresher: RefreshController
    data_paths: DataPaths


def _candidate(video_id: str, rank: int) -> ResearchCandidate:
    return ResearchCandidate(
        video_id=video_id,
        title=f"Candidate {rank}",
        channel_id=f"UCCHANNEL{rank:014d}",
        channel_title=f"Candidate channel {rank}",
        published_at=date(2026, 8, 29 - rank),
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
        matched_queries=(QUERY,),
        original_rank=rank,
        status=CandidateStatus.CANDIDATE,
    )


def _discovery_result() -> DiscoveryResult:
    return DiscoveryResult(
        "yt-dlp",
        1,
        (
            _candidate(ACQUIRED_VIDEO_ID, 1),
            _candidate(NO_TRANSCRIPT_VIDEO_ID, 2),
        ),
        (),
        True,
    )


def _harness(
    tmp_path: Path,
    *,
    reader: MutableEvidenceReader | None = None,
    provider_result: object | None = None,
) -> Harness:
    actual_reader = reader or MutableEvidenceReader()
    provider = StaticDiscoveryProvider(
        _discovery_result() if provider_result is None else provider_result
    )
    acquisition = MixedAcquisitionService()
    refresher = RefreshController(actual_reader)
    store = ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW)
    data_paths = DataPaths.from_root(tmp_path / "source-corpus")
    workflow = ResearchWorkflow(
        store=store,
        evidence_reader=actual_reader,
        discovery_provider=provider,
        acquisition_service=acquisition,  # type: ignore[arg-type]
        data_paths=data_paths,
        index_refresher=refresher,
        now=lambda: NOW,
        session_id_factory=lambda: SESSION_ID,
    )
    return Harness(
        workflow,
        store,
        actual_reader,
        provider,
        acquisition,
        refresher,
        data_paths,
    )


def _start(harness: Harness) -> ResearchResponse:
    return harness.workflow.start(
        topic="AI workflows in product and engineering teams",
        queries=(QUERY,),
        languages=("en",),
        freshness_profile=FreshnessProfile.STANDARD,
    )


def _advance_to_approved(harness: Harness) -> None:
    _start(harness)
    harness.workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-decision-key",
    )
    harness.workflow.discover(SESSION_ID, expected_revision=2)
    harness.workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=(ACQUIRED_VIDEO_ID, NO_TRANSCRIPT_VIDEO_ID),
        idempotency_key="candidate-approval-key",
    )


def _complete_session(harness: Harness) -> ResearchResponse:
    _advance_to_approved(harness)
    reassessed = harness.workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquisition-key",
        language="en",
    )
    assert reassessed.session.revision == 7
    return harness.workflow.decide(
        SESSION_ID,
        expected_revision=7,
        decision="sufficient",
        idempotency_key="sufficient-decision-key",
    )


def _transition_states(harness: Harness) -> list[ResearchState]:
    return [
        event.to_state
        for event in harness.store.get_session_history(SESSION_ID).events
        if event.from_state is None or event.from_state is not event.to_state
    ]


def test_complete_cumulative_research_flow_is_durable_and_source_backed(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)

    started = _start(harness)
    assert (
        started.session.state,
        started.session.revision,
        started.required_user_action,
    ) == (
        ResearchState.AWAITING_SUFFICIENCY,
        1,
        "confirm_sufficiency_or_refresh",
    )
    assert started.assessment is not None
    assert started.assessment.coverage.matched_passages == 1
    assert harness.provider.calls == []

    refresh = harness.workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-decision-key",
    )
    refresh_replay = harness.workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-decision-key",
    )
    assert (refresh.session.state, refresh.session.revision) == (
        ResearchState.DISCOVERING,
        2,
    )
    assert refresh_replay.to_dict() == refresh.to_dict()

    discovered = harness.workflow.discover(SESSION_ID, expected_revision=2)
    assert (
        discovered.session.state,
        discovered.session.revision,
        discovered.required_user_action,
    ) == (
        ResearchState.AWAITING_CANDIDATES,
        3,
        "approve_candidates_or_cancel",
    )
    assert harness.provider.calls == [((QuerySpec(QUERY),), 10)]
    assert [candidate.status for candidate in discovered.candidates or ()] == [
        CandidateStatus.CANDIDATE,
        CandidateStatus.CANDIDATE,
    ]

    approved = harness.workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=(ACQUIRED_VIDEO_ID, NO_TRANSCRIPT_VIDEO_ID),
        idempotency_key="candidate-approval-key",
    )
    approval_replay = harness.workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=(ACQUIRED_VIDEO_ID, NO_TRANSCRIPT_VIDEO_ID),
        idempotency_key="candidate-approval-key",
    )
    assert (approved.session.state, approved.session.revision) == (
        ResearchState.ACQUIRING,
        4,
    )
    assert approval_replay.to_dict() == approved.to_dict()

    reassessed = harness.workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquisition-key",
        language="en",
    )
    assert (
        reassessed.session.state,
        reassessed.session.revision,
        reassessed.required_user_action,
    ) == (
        ResearchState.AWAITING_SUFFICIENCY,
        7,
        "confirm_sufficiency_or_refresh",
    )
    assert reassessed.assessment is not None
    assert reassessed.assessment.coverage.matched_passages == 2
    assert reassessed.assessment.coverage.matched_videos == 2
    assert [call[0] for call in harness.acquisition.calls] == [
        ACQUIRED_VIDEO_ID,
        NO_TRANSCRIPT_VIDEO_ID,
    ]
    assert harness.refresher.calls == [harness.data_paths]

    completed = harness.workflow.decide(
        SESSION_ID,
        expected_revision=7,
        decision="sufficient",
        idempotency_key="sufficient-decision-key",
    )
    assert (
        completed.session.state,
        completed.session.revision,
        completed.required_user_action,
    ) == (ResearchState.COMPLETED, 8, None)

    history = harness.store.get_session_history(SESSION_ID)
    assert [decision.idempotency_key for decision in history.decisions] == [
        "refresh-decision-key",
        "candidate-approval-key",
        "sufficient-decision-key",
    ]
    assert [decision.action for decision in history.decisions] == [
        "refresh",
        "approve_candidates",
        "sufficient",
    ]
    assert len(history.acquisition_attempts) == 1
    assert history.acquisition_attempts[0].idempotency_key == "acquisition-key"
    assert history.acquisition_attempts[0].video_ids == (
        ACQUIRED_VIDEO_ID,
        NO_TRANSCRIPT_VIDEO_ID,
    )
    assert [outcome.status for outcome in history.acquisition_outcomes] == [
        CandidateStatus.ACQUIRED,
        CandidateStatus.NO_TRANSCRIPT,
    ]
    assert {
        candidate.video_id: candidate.status
        for candidate in harness.store.list_candidates(SESSION_ID)
    } == {
        ACQUIRED_VIDEO_ID: CandidateStatus.ACQUIRED,
        NO_TRANSCRIPT_VIDEO_ID: CandidateStatus.NO_TRANSCRIPT,
    }
    assert _transition_states(harness) == [
        ResearchState.ASSESSING,
        ResearchState.AWAITING_SUFFICIENCY,
        ResearchState.DISCOVERING,
        ResearchState.AWAITING_CANDIDATES,
        ResearchState.ACQUIRING,
        ResearchState.REINDEXING,
        ResearchState.ASSESSING,
        ResearchState.AWAITING_SUFFICIENCY,
        ResearchState.COMPLETED,
    ]

    calls_before_export = (harness.reader.passage_calls, harness.reader.video_calls)
    first = harness.workflow.export(
        DossierExportRequest(SESSION_ID, tmp_path / "dossier-one"),
        package_version="0.2.0",
    )
    second = harness.workflow.export(
        DossierExportRequest(SESSION_ID, tmp_path / "dossier-two"),
        package_version="0.2.0",
    )
    first_manifest_bytes = (first.directory / "manifest.json").read_bytes()
    first_dossier_bytes = (first.directory / "dossier.md").read_bytes()
    assert first_manifest_bytes == (second.directory / "manifest.json").read_bytes()
    assert first_dossier_bytes == (second.directory / "dossier.md").read_bytes()
    assert first.manifest_sha256 == hashlib.sha256(first_manifest_bytes).hexdigest()
    assert first.dossier_sha256 == hashlib.sha256(first_dossier_bytes).hexdigest()

    manifest = json.loads(first_manifest_bytes)
    assert [evidence["url"] for evidence in manifest["evidence"]] == [
        LOCAL_URL,
        ACQUIRED_URL,
    ]
    assert [item["status"] for item in manifest["acquisition_outcomes"]] == [
        "acquired",
        "no_transcript",
    ]
    assert manifest["acquisition_outcomes"] == [
        {
            "error_code": None,
            "source_sha256": "c" * 64,
            "status": "acquired",
            "video_id": ACQUIRED_VIDEO_ID,
        },
        {
            "error_code": "transcript_unavailable",
            "source_sha256": None,
            "status": "no_transcript",
            "video_id": NO_TRANSCRIPT_VIDEO_ID,
        },
    ]
    assert str(tmp_path) not in first_manifest_bytes.decode("utf-8")
    assert "dossier.md" not in {
        evidence["excerpt"] for evidence in manifest["evidence"]
    }
    assert (harness.reader.passage_calls, harness.reader.video_calls) == (
        calls_before_export
    )
    assert not first.directory.is_relative_to(harness.data_paths.root)
    with pytest.raises(FileExistsError):
        harness.workflow.export(
            DossierExportRequest(SESSION_ID, first.directory),
            package_version="0.2.0",
        )
    assert first_manifest_bytes == (first.directory / "manifest.json").read_bytes()


def test_empty_local_corpus_still_requires_the_sufficiency_decision(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, reader=MutableEvidenceReader(empty=True))

    response = _start(harness)

    assert response.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert response.session.revision == 1
    assert response.required_user_action == "confirm_sufficiency_or_refresh"
    assert response.assessment is not None
    assert response.assessment.coverage.matched_passages == 0
    assert response.assessment.coverage.matched_videos == 0
    assert response.assessment.coverage.queries_with_zero_hits == (QUERY,)
    assert harness.provider.calls == []
    assert harness.acquisition.calls == []
    assert harness.refresher.calls == []


def test_stale_database_snapshot_fails_retryably_without_mixed_evidence(
    tmp_path: Path,
) -> None:
    harness = _harness(
        tmp_path,
        reader=MutableEvidenceReader(stale_snapshot=True),
    )

    response = _start(harness)

    assert response.session.state is ResearchState.FAILED_RETRYABLE
    assert response.session.revision == 1
    assert response.session.retry_target is ResearchState.ASSESSING
    assert response.assessment is None
    assert response.error_code == "local_index_unavailable"
    assert harness.store.get_session_history(SESSION_ID).assessments == ()


@pytest.mark.parametrize(
    "provider_result",
    [
        pytest.param(
            RuntimeError("provider token=secret-value"),
            id="secret-bearing-provider-error",
        ),
        pytest.param(
            DiscoveryResult("yt-dlp", 1, (), ("no_candidates",), True),
            id="zero-candidates",
        ),
        pytest.param(
            DiscoveryResult(
                "yt-dlp",
                1,
                ({"video_id": "invalid"},),  # type: ignore[arg-type]
                (),
                True,
            ),
            id="invalid-provider-metadata",
        ),
    ],
)
def test_discovery_failures_are_bounded_and_preserve_no_candidate_snapshot(
    tmp_path: Path,
    provider_result: object,
) -> None:
    harness = _harness(tmp_path, provider_result=provider_result)
    _start(harness)
    harness.workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-decision-key",
    )

    response = harness.workflow.discover(SESSION_ID, expected_revision=2)

    assert response.session.state is ResearchState.FAILED_RETRYABLE
    assert response.session.revision == 3
    assert response.session.retry_target is ResearchState.DISCOVERING
    assert response.error_code == "discovery_unavailable"
    assert response.candidates is None
    serialized = json.dumps(response.to_dict()) + "".join(
        event.payload_json
        for event in harness.store.get_session_history(SESSION_ID).events
    )
    assert "secret-value" not in serialized
    assert harness.store.list_candidates(SESSION_ID) == ()


def test_candidate_snapshot_race_rejects_the_second_concurrent_approval(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _start(harness)
    harness.workflow.decide(
        SESSION_ID,
        expected_revision=1,
        decision="refresh",
        idempotency_key="refresh-decision-key",
    )
    harness.workflow.discover(SESSION_ID, expected_revision=2)
    competing = ResearchWorkflow(
        store=harness.store,
        evidence_reader=harness.reader,
        discovery_provider=harness.provider,
        now=lambda: NOW,
        session_id_factory=lambda: "unused-session-id",
    )

    first = harness.workflow.approve(
        SESSION_ID,
        expected_revision=3,
        video_ids=(ACQUIRED_VIDEO_ID,),
        idempotency_key="first-approval-key",
    )
    with pytest.raises(ValueError):
        competing.approve(
            SESSION_ID,
            expected_revision=3,
            video_ids=(NO_TRANSCRIPT_VIDEO_ID,),
            idempotency_key="second-approval-key",
        )

    assert first.session.revision == 4
    statuses = {
        candidate.video_id: candidate.status
        for candidate in harness.store.list_candidates(SESSION_ID)
    }
    assert statuses == {
        ACQUIRED_VIDEO_ID: CandidateStatus.APPROVED,
        NO_TRANSCRIPT_VIDEO_ID: CandidateStatus.CANDIDATE,
    }
    assert [
        decision.idempotency_key
        for decision in harness.store.get_session_history(SESSION_ID).decisions
    ] == ["refresh-decision-key", "first-approval-key"]


def test_failed_refresh_keeps_outcome_and_retry_does_not_reacquire(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _advance_to_approved(harness)
    harness.refresher.fail = True

    failed = harness.workflow.acquire(
        SESSION_ID,
        expected_revision=4,
        idempotency_key="acquisition-key",
        language="en",
    )

    assert failed.session.state is ResearchState.FAILED_RETRYABLE
    assert failed.session.revision == 6
    assert failed.session.retry_target is ResearchState.REINDEXING
    assert failed.error_code == "index_refresh_failed"
    calls_before_retry = tuple(harness.acquisition.calls)
    outcomes_before_retry = harness.store.get_session_history(
        SESSION_ID
    ).acquisition_outcomes
    assert [outcome.status for outcome in outcomes_before_retry] == [
        CandidateStatus.ACQUIRED,
        CandidateStatus.NO_TRANSCRIPT,
    ]

    harness.refresher.fail = False
    recovered = harness.workflow.retry(
        SESSION_ID,
        expected_revision=6,
        idempotency_key="reindex-retry-key",
    )

    assert recovered.session.state is ResearchState.AWAITING_SUFFICIENCY
    assert recovered.session.revision == 9
    assert recovered.required_user_action == "confirm_sufficiency_or_refresh"
    assert tuple(harness.acquisition.calls) == calls_before_retry
    assert len(harness.refresher.calls) == 2
    assert harness.store.get_session_history(
        SESSION_ID
    ).acquisition_outcomes == outcomes_before_retry


def test_dossier_root_identity_rejects_a_path_swap_before_publication(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    _complete_session(harness)
    configured_root = tmp_path / "configured-output"
    configured_root.mkdir()
    original = configured_root.stat()
    retired_root = tmp_path / "retired-output"
    configured_root.rename(retired_root)
    configured_root.mkdir()
    request = DossierExportRequest(
        SESSION_ID,
        configured_root / "dossier",
        root_constraint=DossierRootConstraint(
            configured_root,
            original.st_dev,
            original.st_ino,
        ),
    )

    with pytest.raises(ValueError, match="root changed"):
        harness.workflow.export(request, package_version="0.2.0")

    assert tuple(configured_root.iterdir()) == ()
    assert tuple(retired_root.iterdir()) == ()
