from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime

import pytest

from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.discovery import DiscoveryResult
from yt_insights.research.models import (
    CandidateStatus,
    DatabaseSnapshot,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    VideoEvidence,
    ResearchCandidate,
)
from yt_insights.research.store import ResearchStore
from yt_insights.research.workflow import ResearchWorkflow


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
) -> ResearchWorkflow:
    return ResearchWorkflow(
        store=ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW),
        evidence_reader=reader,
        discovery_provider=provider,
        now=lambda: NOW,
        session_id_factory=lambda: SESSION_ID,
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
    started = workflow.start(
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
