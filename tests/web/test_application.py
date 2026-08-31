from __future__ import annotations

from collections.abc import Callable, Mapping
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
from yt_insights.search.sqlite_fts import SearchIndexNotFound
from yt_insights.web.api import PlanChanged
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
        self.queries: list[object] = []
        self.error: Exception | None = None

    def search(self, query: object) -> tuple[object, ...]:
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


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def list_sessions(self, *, limit: int, offset: int) -> tuple[ResearchSession, ...]:
        self.calls.append((limit, offset))
        now = datetime(2026, 8, 31, 12, tzinfo=UTC)
        return (
            ResearchSession(
                SESSION_ID,
                "Local agents",
                (QuerySpec("local agents"),),
                ("fr",),
                FreshnessProfile.STANDARD,
                "f" * 64,
                ResearchState.AWAITING_SUFFICIENCY,
                RequiredUserAction.CONFIRM_SUFFICIENCY_OR_REFRESH,
                5,
                None,
                now,
                now,
            ),
        )


class FakeExports:
    def list_exports(self, *, limit: int) -> dict[str, object]:
        return {"items": [{"name": "safe-export"}], "limit": limit, "truncated": False}


class FakeWorkflow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.error: Exception | None = None

    def _result(self, name: str, payload: object) -> FakeResponse:
        self.calls.append((name, payload))
        if self.error is not None:
            raise self.error
        return FakeResponse()

    def start(self, **kwargs: object) -> FakeResponse:
        return self._result("start", kwargs)

    def status(self, session_id: str) -> FakeResponse:
        return self._result("status", session_id)

    def decide(self, session_id: str, **kwargs: object) -> FakeResponse:
        return self._result("decide", (session_id, kwargs))

    def discover(self, session_id: str, **kwargs: object) -> FakeResponse:
        return self._result("discover", (session_id, kwargs))

    def approve(self, session_id: str, **kwargs: object) -> FakeResponse:
        return self._result("approve", (session_id, kwargs))

    def acquire(self, session_id: str, **kwargs: object) -> FakeResponse:
        return self._result("acquire", (session_id, kwargs))

    def retry(self, session_id: str, **kwargs: object) -> FakeResponse:
        return self._result("retry", (session_id, kwargs))

    def export(self, request: object, *, package_version: str) -> object:
        self.calls.append(("export", (request, package_version)))
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

    def submit(
        self, kind: str, operation: Callable[[], Mapping[str, object]]
    ) -> JobSnapshot:
        if self.error is not None:
            raise self.error
        self.submissions.append((kind, operation))
        return JobSnapshot("job-1", kind, "queued")

    def get(self, job_id: str) -> JobSnapshot:
        return JobSnapshot(job_id, "discovery", "succeeded", {"done": True})


class FakeSources:
    def __init__(self) -> None:
        self.changed = False
        self.acquired = False

    def preview(self, request: object) -> Mapping[str, object]:
        return {
            "fingerprint": "a" * 64,
            "source_kind": "channel",
            "selected_count": 1,
            "videos": [{"video_id": "abc123DEF45"}],
        }

    def prepare_acquisition(
        self, request: object
    ) -> Callable[[], Mapping[str, object]]:
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
            '"freshness_profile":"standard"}',
        )
    )
    decision = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/decisions",
            '{"expected_revision":4,"decision":"refresh",'
            '"idempotency_key":"decision-1"}',
        )
    )
    approval = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/approvals",
            '{"expected_revision":5,"video_ids":["abc123DEF45"],'
            '"idempotency_key":"approval-1"}',
        )
    )
    discovery = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/discovery",
            '{"expected_revision":5}',
        )
    )
    acquisition = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/acquisition",
            '{"expected_revision":6,"idempotency_key":"acquisition-1","language":"fr"}',
        )
    )
    retry = services.app.handle(
        _post(
            f"/api/v1/research/sessions/{SESSION_ID}/retry",
            '{"expected_revision":7,"idempotency_key":"retry-1"}',
        )
    )

    assert start.status == decision.status == approval.status == 200
    assert discovery.status == acquisition.status == retry.status == 202
    assert [kind for kind, _ in services.jobs.submissions] == [
        "research_discovery",
        "research_acquisition",
        "research_retry",
    ]
    assert services.jobs.submissions[0][1]()["session"]["revision"] == 5


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
            '{"source":"https://www.youtube.com/@example","language":"fr",'
            '"analyze":false,"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
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
            '{"source":"https://www.youtube.com/@example","language":"fr",'
            '"analyze":false,"fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
        )
    )

    assert response.status == 202
    assert response.json_body == {"schema_version": 1, "job_id": "job-1"}
    kind, operation = services.jobs.submissions[-1]
    assert kind == "source_acquisition"
    assert services.sources.acquired is False
    assert operation() == {"selected": 1, "items": []}
    assert services.sources.acquired is True


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
