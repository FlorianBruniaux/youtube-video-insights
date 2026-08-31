from __future__ import annotations

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
from yt_insights.research.store import ResearchRevisionConflict
from yt_insights.search.models import SearchQuery
from yt_insights.search.sqlite_fts import SearchIndexNotFound
from yt_insights.web.api import PlanChanged, SourcePreviewRequest
from yt_insights.web.application import WebApplication
from yt_insights.web.jobs import JobQueueFull, JobSnapshot
from yt_insights.web.models import WebRequest

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


class FakeExports:
    def list_exports(self, *, limit: int) -> dict[str, object]:
        return {"items": [{"name": "safe-export"}], "limit": limit, "truncated": False}


class FakeWorkflow:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.start_calls: list[
            tuple[str, tuple[str, ...], tuple[str, ...], FreshnessProfile]
        ] = []
        self.status_calls: list[str] = []
        self.decision_calls: list[tuple[str, int, str, str]] = []
        self.discovery_calls: list[tuple[str, int]] = []
        self.approval_calls: list[tuple[str, int, tuple[str, ...], str]] = []
        self.acquisition_calls: list[tuple[str, int, str, str]] = []
        self.retry_calls: list[tuple[str, int, str]] = []
        self.export_calls: list[tuple[object, str]] = []

    def _result(self) -> FakeResponse:
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


def test_search_delegates_to_existing_service(services: SimpleNamespace) -> None:
    response = services.app.handle(WebRequest.get("/api/v1/search", "q=local&limit=10"))

    assert response.status == 200
    assert response.json_body["schema_version"] == 1
    assert response.json_body["hits"][0]["url"].startswith("https://youtube.com/watch?")
    assert services.search.queries[0].text == "local"
    assert len(response.json_body["hits"][0]["excerpt"]) == 1_500
    assert len(response.json_body["hits"][0]["title"]) == 300


def test_status_route_returns_the_versioned_liveness_contract(
    services: SimpleNamespace,
) -> None:
    response = services.app.handle(WebRequest.get("/api/v1/status"))

    assert response.status == 200
    assert response.json_body == {"schema_version": 1, "status": "ok"}


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
    assert job.json_body["job"]["result"] == {"done": True}


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
            '{"expected_revision":5}',
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


def test_initial_stale_revision_returns_409_without_queue_submission(
    services: SimpleNamespace,
) -> None:
    services.store.session = _session(state=ResearchState.DISCOVERING, revision=6)

    response = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5}',
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
            '{"expected_revision":5}',
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
            '{"expected_revision":5}',
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
            '{"expected_revision":5}',
        )
    )
    services.workflow.error = ValueError("failed at /Users/private/workflow.sqlite3")

    result = services.jobs.submissions[0][1]()

    assert response.status == 202
    assert result == {"schema_version": 1, "error": {"code": "operation_failed"}}


def test_missing_or_incompatible_queued_session_fails_before_submission(
    services: SimpleNamespace,
) -> None:
    services.store.missing = True
    missing = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5}',
        )
    )
    services.store.missing = False
    services.store.session = _session(
        state=ResearchState.AWAITING_SUFFICIENCY, revision=5
    )
    incompatible = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5}',
        )
    )

    assert missing.status == 404
    assert missing.json_body["error"] == {"code": "not_found"}
    assert incompatible.status == 409
    assert incompatible.json_body["error"] == {"code": "workflow_conflict"}
    assert services.jobs.submissions == []


def test_missing_or_stale_synchronous_mutation_fails_before_workflow(
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
    assert services.workflow.decision_calls == []


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
            '{"expected_revision":5}',
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
