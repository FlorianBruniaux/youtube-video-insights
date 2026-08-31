from __future__ import annotations

from datetime import UTC, datetime

import pytest

from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.models import (
    DatabaseSnapshot,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    VideoEvidence,
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


def _workflow(tmp_path, reader: FakeEvidenceReader) -> ResearchWorkflow:
    return ResearchWorkflow(
        store=ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW),
        evidence_reader=reader,
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


def test_decide_refresh_persists_discovering_without_a_network_provider(tmp_path) -> None:
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
    assert response.to_dict()["error_code"] == "discovery_not_configured"
