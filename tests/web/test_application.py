from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from yt_insights.catalog import CatalogError
from yt_insights.research.models import (
    FreshnessProfile,
    QuerySpec,
    RequiredUserAction,
    ResearchSession,
    ResearchState,
)
from yt_insights.research.store import (
    DecisionReplay,
    DecisionReplayStatus,
    ResearchIdempotencyConflict,
    ResearchRevisionConflict,
    ResearchStore,
)
from yt_insights.search.models import SearchQuery
from yt_insights.search.sqlite_fts import SearchIndexError, SearchIndexNotFound
from yt_insights.web.api import PlanChanged, SourcePreviewRequest
from yt_insights.web.application import WebApplication, _ReplayRegistry
from yt_insights.web.jobs import JobQueueFull, JobSnapshot
from yt_insights.web.models import WebRequest, WebResponse

SESSION_ID = "01K4RESEARCH0000000000000000"


class FakeResponse:
    def __init__(self, session_id: str = SESSION_ID) -> None:
        self.session_id = session_id

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "session": {"session_id": self.session_id, "revision": 5},
            "assessment": None,
            "candidates": None,
            "required_user_action": None,
            "error_code": None,
            "acquisition_history": [],
            "acquisition_history_truncated": False,
        }


class FakeSearch:
    def __init__(self) -> None:
        self.queries: list[SearchQuery] = []
        self.error: Exception | None = None

    def search(self, query: SearchQuery) -> tuple[object, ...]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return (
            SimpleNamespace(
                document=SimpleNamespace(
                    channel_id="channel-id",
                    channel_title="Channel " + "c" * 220,
                    video_title="Title " + "t" * 320,
                    language="fr",
                    source_relpath="corpus/transcript.vtt",
                ),
                passage=SimpleNamespace(
                    passage_id="a" * 64,
                    start_seconds=12.0,
                    end_seconds=18.0,
                    youtube_url="https://youtube.com/watch?v=abc123DEF45&t=12s",
                ),
                rank=1,
                score=-1.25,
                excerpt="e" * 1_600,
            ),
        )


class FakeCatalog:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.error: Exception | None = None

    def list_sources(self, *, limit: int, offset: int) -> dict[str, object]:
        self.calls.append((limit, offset))
        if self.error is not None:
            raise self.error
        return {
            "items": [{"video_id": "abc123DEF45"}],
            "limit": limit,
            "offset": offset,
        }

    def corpus_status(self) -> dict[str, object]:
        return {
            "health": "ready",
            "videos": 12,
            "transcripts": 10,
            "documents_indexed": 10,
            "passages_indexed": 42,
        }


def _session(
    *,
    state: ResearchState = ResearchState.AWAITING_SUFFICIENCY,
    revision: int = 5,
    retry_target: ResearchState | None = None,
) -> ResearchSession:
    now = datetime(2026, 8, 31, 12, tzinfo=UTC)
    return ResearchSession(
        SESSION_ID,
        "Local agents",
        (QuerySpec("local agents"),),
        ("fr",),
        FreshnessProfile.STANDARD,
        "f" * 64,
        state,
        (
            RequiredUserAction.CONFIRM_SUFFICIENCY_OR_REFRESH
            if state is ResearchState.AWAITING_SUFFICIENCY
            else None
        ),
        revision,
        retry_target,
        now,
        now,
    )


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []
        self.get_calls: list[str] = []
        self.session = _session()
        self.missing = False
        self.error: Exception | None = None
        self.decision_replay: DecisionReplay | None = None
        self.decision_replay_error: Exception | None = None
        self.decision_replay_calls: list[tuple[str, int, str, object, str]] = []
        self.acquisition_replay: object | None = None
        self.acquisition_replay_error: Exception | None = None
        self.acquisition_replay_calls: list[tuple[str, int, str, str, str | None]] = []
        self.timeline_callback: Callable[[], None] | None = None

    def list_sessions(self, *, limit: int, offset: int) -> tuple[ResearchSession, ...]:
        self.calls.append((limit, offset))
        return (self.session,)

    def get_session(self, session_id: str) -> ResearchSession:
        self.get_calls.append(session_id)
        if self.error is not None:
            raise self.error
        if self.missing or session_id != SESSION_ID:
            raise ValueError("session does not exist")
        return self.session

    def get_session_history(self, session_id: str) -> object:
        raise AssertionError(f"full history loaded for {session_id}")

    def get_public_timeline(
        self,
        session_id: str,
        *,
        expected_revision: int,
        limit: int,
    ) -> object:
        assert session_id == SESSION_ID
        assert expected_revision == 5
        assert limit == 100
        created_at = datetime(2026, 8, 31, 12, tzinfo=UTC)
        timeline = SimpleNamespace(
            decisions=(
                SimpleNamespace(action="refresh", created_at=created_at),
            ),
            events=(
                SimpleNamespace(
                    event_id=1,
                    from_state=None,
                    to_state=ResearchState.ASSESSING,
                    event_code="session_created",
                    created_at=created_at,
                ),
            ),
            decisions_truncated=False,
            events_truncated=False,
        )
        if self.timeline_callback is not None:
            self.timeline_callback()
        return timeline

    def get_decision_replay(
        self,
        session_id: str,
        *,
        expected_revision: int,
        action: str,
        request: object,
        idempotency_key: str,
    ) -> DecisionReplay | None:
        self.decision_replay_calls.append(
            (session_id, expected_revision, action, request, idempotency_key)
        )
        if self.decision_replay_error is not None:
            raise self.decision_replay_error
        return self.decision_replay

    def get_acquisition_replay(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        language: str,
        cookies_from_browser: str | None,
    ) -> object | None:
        self.acquisition_replay_calls.append(
            (
                session_id,
                expected_revision,
                idempotency_key,
                language,
                cookies_from_browser,
            )
        )
        if self.acquisition_replay_error is not None:
            raise self.acquisition_replay_error
        return self.acquisition_replay


class FakeExports:
    def list_exports(self, *, limit: int) -> dict[str, object]:
        return {
            "items": [
                {
                    "name": "safe-export",
                    "export_id": "a" * 64,
                    "open_url": "/api/v1/exports/" + "a" * 64 + "/dossier",
                }
            ],
            "limit": limit,
            "truncated": False,
        }

    def read_dossier(self, export_id: str) -> bytes | None:
        return b"# Safe dossier\n" if export_id == "a" * 64 else None


class FakeWorkflow:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.before_result: Callable[[], None] | None = None
        self.start_calls: list[
            tuple[str, tuple[str, ...], tuple[str, ...], FreshnessProfile]
        ] = []
        self.status_calls: list[str] = []
        self.decision_calls: list[tuple[str, int, str, str]] = []
        self.discovery_calls: list[tuple[str, int]] = []
        self.approval_calls: list[tuple[str, int, tuple[str, ...], str]] = []
        self.cancellation_calls: list[tuple[str, int, str]] = []
        self.acquisition_calls: list[tuple[str, int, str, str]] = []
        self.retry_calls: list[tuple[str, int, str]] = []
        self.export_calls: list[tuple[object, str]] = []

    def _result(self) -> FakeResponse:
        if self.before_result is not None:
            self.before_result()
        if self.error is not None:
            raise self.error
        return FakeResponse()

    def start(
        self,
        *,
        topic: str,
        queries: tuple[str, ...],
        languages: tuple[str, ...],
        freshness_profile: FreshnessProfile,
    ) -> FakeResponse:
        self.start_calls.append((topic, queries, languages, freshness_profile))
        return self._result()

    def status(self, session_id: str) -> FakeResponse:
        self.status_calls.append(session_id)
        return self._result()

    def decide(
        self,
        session_id: str,
        *,
        expected_revision: int,
        decision: str,
        idempotency_key: str,
    ) -> FakeResponse:
        self.decision_calls.append(
            (session_id, expected_revision, decision, idempotency_key)
        )
        return self._result()

    def discover(self, session_id: str, *, expected_revision: int) -> FakeResponse:
        self.discovery_calls.append((session_id, expected_revision))
        return self._result()

    def approve(
        self,
        session_id: str,
        *,
        expected_revision: int,
        video_ids: tuple[str, ...],
        idempotency_key: str,
    ) -> FakeResponse:
        self.approval_calls.append(
            (session_id, expected_revision, video_ids, idempotency_key)
        )
        return self._result()

    def acquire(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        language: str,
    ) -> FakeResponse:
        self.acquisition_calls.append(
            (session_id, expected_revision, idempotency_key, language)
        )
        return self._result()

    def cancel(
        self, session_id: str, *, expected_revision: int, idempotency_key: str
    ) -> FakeResponse:
        self.cancellation_calls.append(
            (session_id, expected_revision, idempotency_key)
        )
        return self._result()

    def retry(
        self, session_id: str, *, expected_revision: int, idempotency_key: str
    ) -> FakeResponse:
        self.retry_calls.append((session_id, expected_revision, idempotency_key))
        return self._result()

    def export(self, request: object, *, package_version: str) -> object:
        self.export_calls.append((request, package_version))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            directory=Path("/private/output/topic/safe-export"),
            manifest_sha256="a" * 64,
            dossier_sha256="b" * 64,
        )


class FakeJobs:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, Callable[[], Mapping[str, object]]]] = []
        self.error: Exception | None = None
        self.status = "succeeded"

    def submit(
        self, kind: str, operation: Callable[[], Mapping[str, object]]
    ) -> JobSnapshot:
        if self.error is not None:
            raise self.error
        self.submissions.append((kind, operation))
        return JobSnapshot("job-1", kind, "queued")

    def get(self, job_id: str) -> JobSnapshot:
        return JobSnapshot(job_id, "discovery", self.status, {"done": True})


class FakeSources:
    def __init__(self) -> None:
        self.changed = False
        self.acquired = False
        self.preview_calls: list[SourcePreviewRequest] = []
        self.prepare_calls: list[str] = []

    def preview(self, request: SourcePreviewRequest) -> Mapping[str, object]:
        self.preview_calls.append(request)
        return {
            "fingerprint": "a" * 64,
            "source_kind": "channel",
            "selected_count": 1,
            "videos": [{"video_id": "abc123DEF45"}],
        }

    def prepare_acquisition(
        self, fingerprint: str
    ) -> Callable[[], Mapping[str, object]]:
        self.prepare_calls.append(fingerprint)
        if self.changed:
            raise PlanChanged()

        def operation() -> Mapping[str, object]:
            self.acquired = True
            return {"selected": 1, "items": []}

        return operation


@pytest.fixture
def services() -> SimpleNamespace:
    values = SimpleNamespace(
        search=FakeSearch(),
        catalog=FakeCatalog(),
        store=FakeStore(),
        exports=FakeExports(),
        workflow=FakeWorkflow(),
        jobs=FakeJobs(),
        sources=FakeSources(),
    )
    values.app = WebApplication(
        search=values.search,
        catalog=values.catalog,
        workflow=values.workflow,
        research_store=values.store,
        exports=values.exports,
        jobs=values.jobs,
        source_acquisition=values.sources,
        export_request_factory=lambda session_id, force: SimpleNamespace(
            session_id=session_id, force=force
        ),
        package_version="0.2.0",
    )
    return values


def _post(path: str, payload: str) -> WebRequest:
    return WebRequest("POST", path, {}, {}, payload.encode("utf-8"))


def _claimed_retry_application(
    tmp_path: Path,
    services: SimpleNamespace,
) -> tuple[ResearchStore, WebApplication, int, Path]:
    database = tmp_path / "research.sqlite3"
    store = ResearchStore(database)
    store.create_session(
        session_id=SESSION_ID,
        topic="Local AI inference",
        queries=(QuerySpec("local LLM inference"),),
        languages=("en",),
        freshness_profile=FreshnessProfile.FAST,
        discovery_fingerprint="a" * 64,
    )
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
    app = WebApplication(
        search=services.search,
        catalog=services.catalog,
        workflow=services.workflow,
        research_store=store,
        exports=services.exports,
        jobs=services.jobs,
        source_acquisition=services.sources,
        export_request_factory=lambda session_id, force: SimpleNamespace(
            session_id=session_id,
            force=force,
        ),
        package_version="0.2.0",
    )
    return store, app, failed.revision, database


def test_search_delegates_to_existing_service(services: SimpleNamespace) -> None:
    response = services.app.handle(WebRequest.get("/api/v1/search", "q=local&limit=10"))

    assert response.status == 200
    assert response.json_body["schema_version"] == 1
    assert response.json_body["hits"][0]["url"].startswith("https://youtube.com/watch?")
    assert services.search.queries[0].text == "local"
    assert len(response.json_body["hits"][0]["excerpt"]) == 1_500
    assert len(response.json_body["hits"][0]["title"]) == 300
    assert "source" not in response.json_body["hits"][0]


def test_status_route_returns_the_versioned_liveness_contract(
    services: SimpleNamespace,
) -> None:
    response = services.app.handle(WebRequest.get("/api/v1/status"))

    assert response.status == 200
    assert response.json_body == {
        "schema_version": 1,
        "status": "ok",
        "corpus": {
            "health": "ready",
            "videos": 12,
            "transcripts": 10,
            "documents_indexed": 10,
            "passages_indexed": 42,
        },
    }


def test_read_routes_delegate_with_bounded_pagination(
    services: SimpleNamespace,
) -> None:
    sources = services.app.handle(WebRequest.get("/api/v1/sources", "limit=7&offset=2"))
    sessions = services.app.handle(
        WebRequest.get("/api/v1/research/sessions", "limit=6&offset=1")
    )
    exports = services.app.handle(WebRequest.get("/api/v1/exports", "limit=5"))
    session = services.app.handle(
        WebRequest.get(f"/api/v1/research/sessions/{SESSION_ID}")
    )
    job = services.app.handle(WebRequest.get("/api/v1/jobs/job-opaque_1"))

    assert sources.json_body["items"][0]["video_id"] == "abc123DEF45"
    assert services.catalog.calls == [(7, 2)]
    assert sessions.json_body["items"][0]["required_user_action"] == (
        "confirm_sufficiency_or_refresh"
    )
    assert services.store.calls == [(6, 1)]
    assert exports.json_body["items"][0]["name"] == "safe-export"
    assert session.json_body["session"]["session_id"] == SESSION_ID
    assert session.json_body["history"] == {
        "decisions": [
            {"action": "refresh", "created_at": "2026-08-31T12:00:00Z"}
        ],
        "events": [
            {
                "event_id": 1,
                "from_state": None,
                "to_state": "assessing",
                "event_code": "session_created",
                "created_at": "2026-08-31T12:00:00Z",
            }
        ],
        "decisions_truncated": False,
        "events_truncated": False,
    }
    assert job.json_body["job"]["result"] == {"done": True}


def test_export_dossier_is_opened_by_opaque_id_without_a_path(
    services: SimpleNamespace,
) -> None:
    export_id = "a" * 64

    response = services.app.handle(
        WebRequest.get(f"/api/v1/exports/{export_id}/dossier")
    )
    missing = services.app.handle(
        WebRequest.get(f"/api/v1/exports/{'b' * 64}/dossier")
    )

    assert response.status == 200
    assert response.content_type == "text/markdown; charset=utf-8"
    assert response.body == b"# Safe dossier\n"
    assert response.headers == (("Content-Disposition", 'inline; filename="dossier.md"'),)
    assert missing.status == 404


def test_session_detail_fails_closed_when_revision_changes_between_snapshots(
    services: SimpleNamespace,
) -> None:
    """Combining a response and timeline from different revisions creates false history."""
    services.store.timeline_callback = lambda: setattr(
        services.store,
        "session",
        _session(state=ResearchState.DISCOVERING, revision=6),
    )

    response = services.app.handle(
        WebRequest.get(f"/api/v1/research/sessions/{SESSION_ID}")
    )

    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "stale_revision"},
    }


def test_research_mutations_delegate_to_workflow_and_queue_long_jobs(
    services: SimpleNamespace,
) -> None:
    start = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"local","queries":["local"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY, revision=4
    )
    decision = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":4,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )
    services.store.session = _session(
        state=ResearchState.AWAITING_CANDIDATES, revision=5
    )
    approval = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/approvals",
            '{"expected_revision":5,"video_ids":["abc123DEF45"],'
            '"idempotency_key":"approval-1"}',
        )
    )
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    discovery = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-1"}',
        )
    )
    discovery_result = services.jobs.submissions[-1][1]()
    services.store.session = _session(state=ResearchState.ACQUIRING, revision=6)
    acquisition = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/acquisition",
            '{"expected_revision":6,"idempotency_key":"acquisition-1","language":"fr"}',
        )
    )
    acquisition_result = services.jobs.submissions[-1][1]()
    services.store.session = _session(
        state=ResearchState.FAILED_RETRYABLE,
        revision=7,
        retry_target=ResearchState.ASSESSING,
    )
    retry = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            '{"expected_revision":7,"idempotency_key":"retry-1"}',
        )
    )
    retry_result = services.jobs.submissions[-1][1]()

    assert start.status == decision.status == approval.status == 200
    assert discovery.status == acquisition.status == retry.status == 202
    assert services.workflow.start_calls == [
        ("local", ("local",), ("fr",), FreshnessProfile.STANDARD)
    ]
    assert services.workflow.decision_calls == [
        (SESSION_ID, 4, "refresh", "decision-1")
    ]
    assert services.workflow.approval_calls == [
        (SESSION_ID, 5, ("abc123DEF45",), "approval-1")
    ]
    assert [kind for kind, _ in services.jobs.submissions] == [
        "research_discovery",
        "research_acquisition",
        "research_retry",
    ]
    assert discovery_result["session"]["revision"] == 5
    assert acquisition_result["session"]["revision"] == 5
    assert retry_result["session"]["revision"] == 5
    assert services.workflow.discovery_calls == [(SESSION_ID, 5)]
    assert services.workflow.acquisition_calls == [
        (SESSION_ID, 6, "acquisition-1", "fr")
    ]
    assert services.workflow.retry_calls == [(SESSION_ID, 7, "retry-1")]


def test_candidate_cancellation_is_explicit_replayable_and_state_guarded(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(
        state=ResearchState.AWAITING_CANDIDATES, revision=5
    )
    request = _post(
        f"/api/v1/research/sessions/{SESSION_ID}/cancellations",
        '{"expected_revision":5,"idempotency_key":"cancel-1"}',
    )

    first = services.app.handle(request)
    replayed = services.app.handle(request)
    changed = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/cancellations",
            '{"expected_revision":4,"idempotency_key":"cancel-1"}',
        )
    )

    assert first.status == replayed.status == 200
    assert first == replayed
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}
    assert services.workflow.cancellation_calls == [
        (SESSION_ID, 5, "cancel-1"),
    ]

    services.store.session = _session(state=ResearchState.ACQUIRING, revision=6)
    services.workflow.error = ValueError("private incompatible state")
    incompatible = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/cancellations",
            '{"expected_revision":6,"idempotency_key":"cancel-3"}',
        )
    )
    assert incompatible.status == 409
    assert incompatible.json_body["error"] == {"code": "workflow_conflict"}


@pytest.mark.parametrize(
    ("action", "payload", "changed_payload"),
    (
        (
            "discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
            '{"expected_revision":4,"idempotency_key":"discovery-5"}',
        ),
        (
            "acquisition",
            '{"expected_revision":5,"idempotency_key":"acquisition-5","language":"fr"}',
            '{"expected_revision":5,"idempotency_key":"acquisition-5","language":"en"}',
        ),
        (
            "retry",
            '{"expected_revision":5,"idempotency_key":"retry-5"}',
            '{"expected_revision":4,"idempotency_key":"retry-5"}',
        ),
    ),
)
def test_research_job_admission_replays_same_202_and_rejects_payload_change(
    services: SimpleNamespace,
    action: str,
    payload: str,
    changed_payload: str,
) -> None:
    state = {
        "discovery": ResearchState.DISCOVERING,
        "acquisition": ResearchState.ACQUIRING,
        "retry": ResearchState.FAILED_RETRYABLE,
    }[action]
    services.store.session = _session(
        state=state,
        revision=5,
        retry_target=(
            ResearchState.ASSESSING
            if state is ResearchState.FAILED_RETRYABLE
            else None
        ),
    )
    request = _post(
        f"/api/v1/research/sessions/{SESSION_ID}/{action}", payload
    )

    first = services.app.handle(request)
    replayed = services.app.handle(request)
    changed = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/{action}",
            changed_payload,
        )
    )

    assert first.status == replayed.status == 202
    assert first == replayed
    assert len(services.jobs.submissions) == 1
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}


def test_failed_research_job_admission_releases_the_process_replay_key(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    services.jobs.error = JobQueueFull()
    request = _post(
        f"/api/v1/research/sessions/{SESSION_ID}/discovery",
        '{"expected_revision":5,"idempotency_key":"discovery-retry"}',
    )

    rejected = services.app.handle(request)
    services.jobs.error = None
    accepted = services.app.handle(request)

    assert rejected.status == 429
    assert rejected.json_body["error"] == {"code": "job_queue_full"}
    assert accepted.status == 202
    assert len(services.jobs.submissions) == 1


def test_initial_stale_revision_returns_409_without_queue_submission(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=6)

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )

    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "stale_revision"},
    }
    assert services.jobs.submissions == []
    assert services.workflow.discovery_calls == []


def test_revision_race_after_admission_is_a_bounded_terminal_job_result(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.store.session = _session(
        state=ResearchState.AWAITING_CANDIDATES, revision=6
    )

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {"schema_version": 1, "error": {"code": "stale_revision"}}
    assert services.workflow.discovery_calls == []


def test_domain_revision_conflict_inside_job_is_a_bounded_terminal_result(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.workflow.error = ResearchRevisionConflict("private race detail")

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {"schema_version": 1, "error": {"code": "stale_revision"}}


def test_unexpected_workflow_value_error_inside_job_is_operation_failed(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.workflow.error = ValueError("failed at /Users/private/workflow.sqlite3")

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {"schema_version": 1, "error": {"code": "operation_failed"}}


def test_revision_race_inside_queued_work_is_stale_revision(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.workflow.before_result = lambda: setattr(
        services.store,
        "session",
        _session(state=ResearchState.AWAITING_CANDIDATES, revision=6),
    )
    services.workflow.error = ValueError("private domain race")

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {"schema_version": 1, "error": {"code": "stale_revision"}}


def test_state_race_inside_queued_work_is_workflow_conflict(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.workflow.before_result = lambda: setattr(
        services.store,
        "session",
        _session(state=ResearchState.AWAITING_CANDIDATES, revision=5),
    )
    services.workflow.error = ValueError("private domain race")

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {
        "schema_version": 1,
        "error": {"code": "workflow_conflict"},
    }


def test_queued_acquisition_preserves_an_exact_durable_replay(
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY,
        revision=9,
    )
    services.store.acquisition_replay = SimpleNamespace(
        idempotency_key="acquisition-1",
        revision=6,
        language="fr",
        cookies_from_browser=None,
        status="completed",
    )
    monkeypatch.setattr(
        services.store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/acquisition",
            '{"expected_revision":6,"idempotency_key":"acquisition-1","language":"fr"}',
        )
    )

    assert response.status == 202
    result = services.jobs.submissions[0][1]()
    assert result["session"]["revision"] == 5
    assert services.workflow.acquisition_calls == [
        (SESSION_ID, 6, "acquisition-1", "fr")
    ]
    assert services.store.acquisition_replay_calls == [
        (SESSION_ID, 6, "acquisition-1", "fr", None),
        (SESSION_ID, 6, "acquisition-1", "fr", None),
    ]
    assert services.store.get_calls == [SESSION_ID, SESSION_ID]


def test_queued_retry_preserves_an_exact_durable_replay(
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services.store.session = _session(state=ResearchState.COMPLETED, revision=10)
    services.store.decision_replay = DecisionReplay(
        DecisionReplayStatus.COMPLETED,
        _session(
            state=ResearchState.ASSESSING,
            revision=8,
        ),
    )
    monkeypatch.setattr(
        services.store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            '{"expected_revision":7,"idempotency_key":"retry-1"}',
        )
    )

    assert response.status == 202
    result = services.jobs.submissions[0][1]()
    assert result["session"]["revision"] == 5
    assert services.workflow.retry_calls == [(SESSION_ID, 7, "retry-1")]
    assert services.store.decision_replay_calls == [
        (SESSION_ID, 7, "retry", {"expected_revision": 7}, "retry-1"),
        (SESSION_ID, 7, "retry", {"expected_revision": 7}, "retry-1"),
    ]
    assert services.store.get_calls == [SESSION_ID, SESSION_ID]


def test_queued_retry_admits_a_real_in_progress_reservation_without_history(
    tmp_path: Path,
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, app, expected_revision, _database = _claimed_retry_application(
        tmp_path,
        services,
    )
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            json.dumps(
                {
                    "expected_revision": expected_revision,
                    "idempotency_key": "retry-in-progress",
                }
            ),
        )
    )

    assert response.status == 202
    result = services.jobs.submissions[0][1]()
    assert result["session"]["revision"] == 5
    assert services.workflow.retry_calls == [
        (SESSION_ID, expected_revision, "retry-in-progress")
    ]


def test_real_retry_replay_payload_mismatch_remains_stale_without_history(
    tmp_path: Path,
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, app, expected_revision, _database = _claimed_retry_application(
        tmp_path,
        services,
    )
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            json.dumps(
                {
                    "expected_revision": expected_revision - 1,
                    "idempotency_key": "retry-in-progress",
                }
            ),
        )
    )

    assert response.status == 409
    assert response.json_body["error"] == {"code": "stale_revision"}
    assert services.jobs.submissions == []


def test_real_retry_replay_corrupt_null_envelope_is_internal_without_history(
    tmp_path: Path,
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, app, expected_revision, database = _claimed_retry_application(
        tmp_path,
        services,
    )
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM research_decisions WHERE idempotency_key = ?",
            ("retry-in-progress",),
        ).fetchone()
        assert row is not None
        envelope = json.loads(row[0])
        envelope["claim"] = None
        connection.execute(
            "UPDATE research_decisions SET payload_json = ? WHERE idempotency_key = ?",
            (json.dumps(envelope), "retry-in-progress"),
        )
    monkeypatch.setattr(
        store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            json.dumps(
                {
                    "expected_revision": expected_revision,
                    "idempotency_key": "retry-in-progress",
                }
            ),
        )
    )

    assert response.status == 500
    assert response.json_body["error"] == {"code": "internal_error"}
    assert services.jobs.submissions == []


@pytest.mark.parametrize(
    ("action", "payload", "error_attribute"),
    (
        (
            "acquisition",
            '{"expected_revision":6,"idempotency_key":"acquisition-1","language":"fr"}',
            "acquisition_replay_error",
        ),
        (
            "retry",
            '{"expected_revision":7,"idempotency_key":"retry-1"}',
            "decision_replay_error",
        ),
    ),
)
def test_replay_payload_mismatch_does_not_admit_a_stale_request(
    services: SimpleNamespace,
    action: str,
    payload: str,
    error_attribute: str,
) -> None:
    services.store.session = _session(state=ResearchState.COMPLETED, revision=10)
    setattr(services.store, error_attribute, ResearchIdempotencyConflict())

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/{action}",
            payload,
        )
    )

    assert response.status == 409
    assert response.json_body["error"] == {"code": "stale_revision"}
    assert services.jobs.submissions == []


@pytest.mark.parametrize(
    ("action", "payload", "error_attribute"),
    (
        (
            "acquisition",
            '{"expected_revision":6,"idempotency_key":"acquisition-1","language":"fr"}',
            "acquisition_replay_error",
        ),
        (
            "retry",
            '{"expected_revision":7,"idempotency_key":"retry-1"}',
            "decision_replay_error",
        ),
    ),
)
def test_unexpected_targeted_replay_failure_is_internal(
    services: SimpleNamespace,
    action: str,
    payload: str,
    error_attribute: str,
) -> None:
    services.store.session = _session(state=ResearchState.COMPLETED, revision=10)
    setattr(services.store, error_attribute, ValueError("corrupt private payload"))

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/{action}",
            payload,
        )
    )

    assert response.status == 500
    assert response.json_body["error"] == {"code": "internal_error"}
    assert b"corrupt private payload" not in response.body
    assert services.jobs.submissions == []


def test_missing_or_incompatible_queued_session_fails_before_submission(
    services: SimpleNamespace,
) -> None:
    services.store.missing = True
    missing = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.store.missing = False
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY, revision=5
    )
    incompatible = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )

    assert missing.status == 404
    assert missing.json_body["error"] == {"code": "not_found"}
    assert incompatible.status == 409
    assert incompatible.json_body["error"] == {"code": "workflow_conflict"}
    assert services.jobs.submissions == []


def test_missing_synchronous_mutation_skips_workflow_and_stale_is_reclassified(
    services: SimpleNamespace,
) -> None:
    services.store.missing = True
    missing = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )
    services.store.missing = False
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY, revision=6
    )
    services.workflow.error = ValueError("private stale detail")
    stale = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert missing.status == 404
    assert missing.json_body["error"] == {"code": "not_found"}
    assert stale.status == 409
    assert stale.json_body["error"] == {"code": "stale_revision"}
    assert services.workflow.decision_calls == [
        (SESSION_ID, 5, "refresh", "decision-1")
    ]


def test_missing_session_and_unexpected_store_value_error_are_distinct(
    services: SimpleNamespace,
) -> None:
    request = _post(
        f"/api/v1/research/sessions/{SESSION_ID}/decisions",
        '{"expected_revision":5,"decision":"refresh","idempotency_key":"decision-1"}',
    )
    services.store.missing = True
    missing = services.app.handle(request)
    services.store.missing = False
    services.store.error = ValueError(
        "database schema failed at /Users/private/research.sqlite3"
    )
    unexpected = services.app.handle(request)

    assert missing.status == 404
    assert missing.json_body == {
        "schema_version": 1,
        "error": {"code": "not_found"},
    }
    assert unexpected.status == 500
    assert unexpected.json_body == {
        "schema_version": 1,
        "error": {"code": "internal_error"},
    }
    assert b"/Users/private" not in unexpected.body
    assert services.workflow.decision_calls == []


def test_unexpected_synchronous_workflow_value_error_is_internal(
    services: SimpleNamespace,
) -> None:
    services.workflow.error = ValueError(
        "workflow failed at /Users/private/research.sqlite3"
    )

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 500
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "internal_error"},
    }
    assert b"/Users/private" not in response.body


def test_synchronous_wrong_state_is_workflow_conflict(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    services.workflow.error = ValueError("session is in another state")

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "workflow_conflict"},
    }


def test_synchronous_post_preflight_revision_race_is_stale(
    services: SimpleNamespace,
) -> None:
    services.workflow.before_result = lambda: setattr(
        services.store,
        "session",
        _session(state=ResearchState.DISCOVERING, revision=6),
    )
    services.workflow.error = ValueError("private domain race")

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 409
    assert response.json_body["error"] == {"code": "stale_revision"}


def test_synchronous_post_preflight_state_race_is_workflow_conflict(
    services: SimpleNamespace,
) -> None:
    services.workflow.before_result = lambda: setattr(
        services.store,
        "session",
        _session(state=ResearchState.DISCOVERING, revision=5),
    )
    services.workflow.error = ValueError("private domain race")

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 409
    assert response.json_body["error"] == {"code": "workflow_conflict"}


def test_synchronous_decision_allows_the_domain_to_authorize_exact_replay(
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    services.store.session = _session(
        state=ResearchState.AWAITING_CANDIDATES,
        revision=7,
    )
    monkeypatch.setattr(
        services.store,
        "get_session_history",
        lambda _session_id: pytest.fail("full session history was loaded"),
    )

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":5,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 200
    assert services.workflow.decision_calls == [
        (SESSION_ID, 5, "refresh", "decision-1")
    ]


def test_export_wrong_state_is_workflow_conflict(services: SimpleNamespace) -> None:
    services.store.session = _session(state=ResearchState.ACQUIRING, revision=5)
    services.workflow.error = ValueError("not exportable")

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/exports",
            '{"force":false}',
        )
    )

    assert response.status == 409
    assert response.json_body["error"] == {"code": "workflow_conflict"}


def test_session_creation_replays_same_key_and_rejects_changed_payload(
    services: SimpleNamespace,
) -> None:
    first = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"local","queries":["local"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )
    replayed = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"local","queries":["local"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )
    changed = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"changed","queries":["changed"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )

    assert replayed == first
    assert len(services.workflow.start_calls) == 1
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}


def test_session_creation_replays_failure_after_a_durable_side_effect(
    services: SimpleNamespace,
) -> None:
    """A post-commit exception must never permit a duplicate session retry."""
    services.workflow.error = RuntimeError("failed after durable session create")
    original = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    first = services.app.handle(original)
    replayed = services.app.handle(original)
    changed = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"changed","queries":["changed"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )

    assert first == replayed
    assert first.status == 500
    assert first.json_body == {
        "schema_version": 1,
        "error": {"code": "internal_error"},
    }
    assert services.workflow.start_calls == [
        ("local", ("local",), ("fr",), FreshnessProfile.STANDARD)
    ]
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}


def test_session_creation_replays_research_unavailable_failure(
    services: SimpleNamespace,
) -> None:
    services.workflow.error = sqlite3.OperationalError("private database path")
    original = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    first = services.app.handle(original)
    replayed = services.app.handle(original)
    changed = services.app.handle(
        _post(
            "/api/v1/research/sessions",
            '{"topic":"changed","queries":["changed"],"languages":["fr"],'
            '"freshness_profile":"standard","idempotency_key":"start-1"}',
        )
    )

    assert first == replayed
    assert first.status == 503
    assert first.json_body == {
        "schema_version": 1,
        "error": {"code": "research_unavailable"},
    }
    assert services.workflow.start_calls == [
        ("local", ("local",), ("fr",), FreshnessProfile.STANDARD)
    ]
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}


def test_session_failure_replay_prevents_a_second_real_store_insert(
    services: SimpleNamespace,
    tmp_path: Path,
) -> None:
    store = ResearchStore(tmp_path / "research.sqlite3")

    class DurableFailingWorkflow:
        def __init__(self) -> None:
            self.calls = 0

        def start(
            self,
            *,
            topic: str,
            queries: tuple[str, ...],
            languages: tuple[str, ...],
            freshness_profile: FreshnessProfile,
        ) -> object:
            self.calls += 1
            store.create_session(
                session_id=f"durable-session-{self.calls}",
                topic=topic,
                queries=tuple(QuerySpec(query) for query in queries),
                languages=languages,
                freshness_profile=freshness_profile,
                discovery_fingerprint="a" * 64,
            )
            raise RuntimeError("failed after durable insert")

    workflow = DurableFailingWorkflow()
    services.app = WebApplication(
        search=services.search,
        catalog=services.catalog,
        workflow=workflow,  # type: ignore[arg-type]
        research_store=store,
        exports=services.exports,
        jobs=services.jobs,
        source_acquisition=services.sources,
        package_version="0.2.0",
    )
    request = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    first = services.app.handle(request)
    replayed = services.app.handle(request)

    assert first == replayed
    assert workflow.calls == 1
    assert len(store.list_sessions(limit=10, offset=0)) == 1


def test_concurrent_session_failure_executes_the_side_effect_once(
    services: SimpleNamespace,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def fail_after_reservation() -> None:
        entered.set()
        release.wait(timeout=2.0)

    services.workflow.before_result = fail_after_reservation
    services.workflow.error = RuntimeError("failed after durable session create")
    request = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(services.app.handle, request)
        assert entered.wait(timeout=1.0)
        concurrent = [pool.submit(services.app.handle, request) for _ in range(7)]
        release.set()
        responses = (first.result(), *(future.result() for future in concurrent))

    assert all(response == responses[0] for response in responses)
    assert responses[0].status == 500
    assert len(services.workflow.start_calls) == 1


def test_attached_waiter_replays_evicted_record_without_reexecution(
    services: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EvictionInterleavingCondition(threading.Condition):
        def __init__(self) -> None:
            super().__init__()
            self.waiter_attached = threading.Event()
            self.waiter_notified_and_released = threading.Event()
            self.allow_waiter_reacquire = threading.Event()

        def wait(self, timeout: float | None = None) -> bool:
            is_attached_waiter = threading.current_thread().name.startswith(
                "attached-waiter"
            )
            if is_attached_waiter:
                self.waiter_attached.set()
            notified = super().wait(timeout)
            if is_attached_waiter and notified:
                self.release()
                try:
                    self.waiter_notified_and_released.set()
                    if not self.allow_waiter_reacquire.wait(timeout=1.0):
                        raise AssertionError("waiter reacquire gate timed out")
                finally:
                    self.acquire()
            return notified

    condition = EvictionInterleavingCondition()
    registry = _ReplayRegistry(maximum=1, jobs=services.jobs)
    monkeypatch.setattr(registry, "_condition", condition)
    owner_entered = threading.Event()
    release_owner = threading.Event()
    owner_calls = 0

    def owner_operation() -> WebResponse:
        nonlocal owner_calls
        owner_calls += 1
        owner_entered.set()
        if not release_owner.wait(timeout=1.0):
            raise AssertionError("owner release gate timed out")
        return WebResponse.json(200, {"owner_call": owner_calls})

    def run_owner() -> WebResponse:
        return registry.run(
            route="/owner",
            key="same-key",
            payload=("same-payload",),
            operation=owner_operation,
        )

    with (
        ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="owner"
        ) as owner_pool,
        ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="attached-waiter"
        ) as waiter_pool,
    ):
        owner = owner_pool.submit(run_owner)
        assert owner_entered.wait(timeout=1.0)
        waiter = waiter_pool.submit(run_owner)
        assert condition.waiter_attached.wait(timeout=1.0)
        release_owner.set()
        assert condition.waiter_notified_and_released.wait(timeout=1.0)

        third = registry.run(
            route="/third",
            key="third-key",
            payload=("third-payload",),
            operation=lambda: WebResponse.json(200, {"third": True}),
        )
        condition.allow_waiter_reacquire.set()
        first = owner.result(timeout=1.0)
        replayed = waiter.result(timeout=1.0)

    assert third.json_body == {"third": True}
    assert first == replayed
    assert first.json_body == {"owner_call": 1}
    assert owner_calls == 1


def test_pending_session_replay_wait_is_bounded_and_never_reexecutes(
    services: SimpleNamespace,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def block_start() -> None:
        entered.set()
        release.wait(timeout=2.0)

    services.workflow.before_result = block_start
    request = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(services.app.handle, request)
        assert entered.wait(timeout=1.0)
        pending = pool.submit(services.app.handle, request)
        try:
            pending_response = pending.result(timeout=1.0)
        finally:
            release.set()
        first_response = first.result(timeout=1.0)

    replayed = services.app.handle(request)

    assert pending_response.status == 409
    assert pending_response.json_body["error"] == {"code": "request_in_progress"}
    assert replayed == first_response
    assert len(services.workflow.start_calls) == 1


def test_replay_registry_is_thread_safe_for_same_session_creation(
    services: SimpleNamespace,
) -> None:
    request = _post(
        "/api/v1/research/sessions",
        '{"topic":"local","queries":["local"],"languages":["fr"],'
        '"freshness_profile":"standard","idempotency_key":"start-1"}',
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = tuple(pool.map(services.app.handle, (request,) * 16))

    assert all(response == responses[0] for response in responses)
    assert len(services.workflow.start_calls) == 1


def test_replay_registry_evicts_only_after_a_job_is_terminal(
    services: SimpleNamespace,
) -> None:
    services.jobs.status = "queued"
    services.app = WebApplication(
        search=services.search,
        catalog=services.catalog,
        workflow=services.workflow,
        research_store=services.store,
        exports=services.exports,
        jobs=services.jobs,
        source_acquisition=services.sources,
        export_request_factory=lambda session_id, force: SimpleNamespace(
            session_id=session_id, force=force
        ),
        package_version="0.2.0",
        max_replay_records=1,
    )
    first = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )
    full = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            '"idempotency_key":"source-acquisition-2"}',
        )
    )
    services.jobs.status = "failed"
    admitted = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            '"idempotency_key":"source-acquisition-2"}',
        )
    )

    assert first.status == admitted.status == 202
    assert full.status == 429
    assert len(services.jobs.submissions) == 2


def test_source_preview_is_queued_and_returns_only_a_safe_plan(
    services: SimpleNamespace,
) -> None:
    response = services.app.handle(
        _post(
            "/api/v1/sources/preview",
            '{"source":"https://www.youtube.com/@example","language":"fr",'
            '"analyze":false}',
        )
    )

    assert response.status == 202
    assert response.json_body == {"schema_version": 1, "job_id": "job-1"}
    kind, operation = services.jobs.submissions[-1]
    assert kind == "source_preview"
    result = operation()
    assert result["fingerprint"] == "a" * 64
    assert not any("dir" in key or "path" in key for key in result)


def test_changed_source_plan_is_a_fixed_conflict_and_never_acquires(
    services: SimpleNamespace,
) -> None:
    services.sources.changed = True
    response = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )

    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "plan_changed"},
    }
    assert services.sources.acquired is False
    assert services.jobs.submissions == []


def test_confirmed_source_plan_is_queued_for_direct_domain_execution(
    services: SimpleNamespace,
) -> None:
    response = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )

    assert response.status == 202
    assert response.json_body == {"schema_version": 1, "job_id": "job-1"}
    kind, operation = services.jobs.submissions[-1]
    assert kind == "source_acquisition"
    assert services.sources.acquired is False
    assert operation() == {"selected": 1, "items": []}
    assert services.sources.acquired is True


def test_source_acquisition_replays_admission_and_rejects_changed_payload(
    services: SimpleNamespace,
) -> None:
    first = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )
    replayed = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )
    changed = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
            'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",'
            '"idempotency_key":"source-acquisition-1"}',
        )
    )

    assert replayed == first
    assert len(services.jobs.submissions) == 1
    assert services.sources.prepare_calls == ["a" * 64]
    assert changed.status == 409
    assert changed.json_body["error"] == {"code": "idempotency_conflict"}


def test_replayed_source_admission_survives_plan_eviction_but_a_new_key_does_not(
    services: SimpleNamespace,
) -> None:
    payload = (
        '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"idempotency_key":"source-acquisition-1"}'
    )
    first = services.app.handle(_post("/api/v1/sources/acquire", payload))
    services.sources.changed = True

    replayed = services.app.handle(_post("/api/v1/sources/acquire", payload))
    missing = services.app.handle(
        _post(
            "/api/v1/sources/acquire",
            '{"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
            '"idempotency_key":"source-acquisition-2"}',
        )
    )

    assert replayed == first
    assert missing.status == 409
    assert missing.json_body["error"] == {"code": "plan_changed"}
    assert services.sources.prepare_calls == ["a" * 64, "a" * 64]
    assert len(services.jobs.submissions) == 1


def test_stale_revision_is_a_fixed_conflict(services: SimpleNamespace) -> None:
    services.workflow.error = ResearchRevisionConflict("private state")
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":4,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "stale_revision"},
    }


def test_fixed_errors_never_copy_exception_paths(services: SimpleNamespace) -> None:
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY, revision=4
    )
    services.workflow.error = RuntimeError("failed at /Users/private/secret.sqlite3")
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":4,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )

    assert response.status == 500
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "internal_error"},
    }
    assert b"/Users/" not in response.body


def test_known_capacity_and_index_failures_have_fixed_public_codes(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=5)
    services.jobs.error = JobQueueFull("private queue detail")
    queued = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5,"idempotency_key":"discovery-5"}',
        )
    )
    services.search.error = SearchIndexNotFound("/private/search.sqlite3")
    unavailable = services.app.handle(WebRequest.get("/api/v1/search", "q=local"))

    assert queued.status == 429
    assert queued.json_body["error"] == {"code": "job_queue_full"}
    assert unavailable.status == 503
    assert unavailable.json_body["error"] == {"code": "search_unavailable"}


def test_invalid_and_unknown_routes_do_not_reach_dependencies(
    services: SimpleNamespace,
) -> None:
    invalid = services.app.handle(WebRequest.get("/api/v1/search", "q=one&q=two"))
    unknown = services.app.handle(WebRequest.get("/api/v1/private"))
    unsafe_session = services.app.handle(
        WebRequest.get("/api/v1/research/sessions/..%2Fsecret")
    )

    assert invalid.status == 400
    assert invalid.json_body["error"] == {"code": "invalid_request"}
    assert unknown.status == 404
    assert unsafe_session.status == 400
    assert unsafe_session.json_body["error"] == {"code": "invalid_request"}
    assert services.search.queries == []


def test_oversized_query_and_json_integers_are_invalid_requests(
    services: SimpleNamespace,
) -> None:
    oversized_integer = "9" * 5_000

    query_response = services.app.handle(
        WebRequest.get("/api/v1/sources", f"offset={oversized_integer}")
    )
    json_response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":'
            + oversized_integer
            + ',"decision":"refresh","idempotency_key":"decision-1"}',
        )
    )

    assert query_response.status == json_response.status == 400
    assert query_response.json_body["error"] == {"code": "invalid_request"}
    assert json_response.json_body["error"] == {"code": "invalid_request"}
    assert services.catalog.calls == []
    assert services.workflow.decision_calls == []


def test_catalog_unavailability_uses_a_bounded_error(services: SimpleNamespace) -> None:
    services.catalog.error = CatalogError("/private/catalog.sqlite3")
    response = services.app.handle(WebRequest.get("/api/v1/sources"))

    assert response.status == 503
    assert response.json_body["error"] == {"code": "catalog_unavailable"}


def test_source_index_unavailability_uses_the_search_error_mapping(
    services: SimpleNamespace,
) -> None:
    services.catalog.error = SearchIndexError("/private/search.sqlite3")

    response = services.app.handle(WebRequest.get("/api/v1/sources"))

    assert response.status == 503
    assert response.json_body["error"] == {"code": "search_unavailable"}
    assert b"/private/" not in response.body


def test_export_response_omits_the_output_directory(services: SimpleNamespace) -> None:
    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/exports",
            '{"force":false}',
        )
    )

    assert response.status == 200
    assert response.json_body["export"] == {
        "name": "safe-export",
        "manifest_sha256": "a" * 64,
        "dossier_sha256": "b" * 64,
    }
    assert b"/private/output" not in response.body
