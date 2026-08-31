"""Framework-neutral route orchestration for the versioned local API."""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from yt_insights.catalog import CatalogError
from yt_insights.research.dossier import DossierExportRequest, DossierExportResult
from yt_insights.research.store import ResearchRevisionConflict, ResearchStore
from yt_insights.research.workflow import ResearchResponse, ResearchWorkflow
from yt_insights.search.service import SearchService
from yt_insights.search.sqlite_fts import SearchIndexError

from .api import (
    PlanChanged,
    RequestValidationError,
    SourceAcquisitionRequest,
    SourcePreviewRequest,
    job_payload,
    parse_acquisition,
    parse_approval,
    parse_decision,
    parse_export,
    parse_pagination,
    parse_retry,
    parse_revision,
    parse_search,
    parse_source_acquisition,
    parse_source_preview,
    parse_start_session,
    research_session_payload,
    safe_export_payload,
    search_payload,
    validate_job_id,
    validate_session_id,
)
from .jobs import (
    JobExecutorClosed,
    JobNotFound,
    JobOperation,
    JobQueueFull,
    JobSnapshot,
)
from .models import WebRequest, WebResponse
from .readers import CatalogWebReader, ExportReader

_LOGGER = logging.getLogger(__name__)
_SESSION_ROUTE = re.compile(r"/api/v1/research/sessions/([^/]+)")
_SESSION_ACTION_ROUTE = re.compile(
    r"/api/v1/research/sessions/([^/]+)/(decisions|discovery|approvals|acquisition|retry|exports)"
)
_JOB_ROUTE = re.compile(r"/api/v1/jobs/([^/]+)")


class JobSubmitter(Protocol):
    """The only queue capability used by mutation route helpers."""

    def submit(self, kind: str, operation: JobOperation) -> JobSnapshot: ...


@runtime_checkable
class JobReader(Protocol):
    def get(self, job_id: str) -> JobSnapshot: ...


class SourceAcquisition(Protocol):
    def preview(self, request: SourcePreviewRequest) -> Mapping[str, object]: ...

    def prepare_acquisition(
        self, request: SourceAcquisitionRequest
    ) -> Callable[[], Mapping[str, object]]: ...


ExportRequestFactory = Callable[[str, bool], DossierExportRequest]


class WebApplication:
    """Dispatch versioned API routes directly to existing domain services."""

    def __init__(
        self,
        *,
        search: SearchService,
        catalog: CatalogWebReader,
        workflow: ResearchWorkflow,
        research_store: ResearchStore,
        exports: ExportReader,
        jobs: JobSubmitter,
        source_acquisition: SourceAcquisition | None = None,
        export_request_factory: ExportRequestFactory | None = None,
        package_version: str,
        job_reader: JobReader | None = None,
    ) -> None:
        resolved_reader = job_reader
        if resolved_reader is None:
            if not isinstance(jobs, JobReader):
                raise TypeError("jobs must expose a reader or one must be supplied")
            resolved_reader = jobs
        self._search = search
        self._catalog = catalog
        self._workflow = workflow
        self._research_store = research_store
        self._exports = exports
        self._jobs = jobs
        self._job_reader = resolved_reader
        self._source_acquisition = source_acquisition
        self._export_request_factory = export_request_factory
        self._package_version = package_version

    def handle(self, request: WebRequest) -> WebResponse:
        """Return a fixed public response without reflecting exception text."""
        try:
            return self._dispatch(request)
        except RequestValidationError:
            return _error(400, "invalid_request")
        except JobNotFound:
            return _error(404, "not_found")
        except PlanChanged:
            return _error(409, "plan_changed")
        except ResearchRevisionConflict:
            return _error(409, "stale_revision")
        except JobQueueFull:
            return _error(429, "job_queue_full")
        except JobExecutorClosed:
            return _error(503, "jobs_unavailable")
        except SearchIndexError:
            return _error(503, "search_unavailable")
        except CatalogError:
            return _error(503, "catalog_unavailable")
        except sqlite3.Error:
            return _error(503, "research_unavailable")
        except ValueError:
            return _error(409, "workflow_conflict")
        except Exception as exc:
            _LOGGER.error("web request failed: %s", type(exc).__name__)
            return _error(500, "internal_error")

    def _dispatch(self, request: WebRequest) -> WebResponse:
        if request.method == "GET":
            return self._get(request)
        if request.method == "POST":
            return self._post(request)
        return _error(404, "not_found")

    def _get(self, request: WebRequest) -> WebResponse:
        if request.path == "/api/v1/status":
            _require_empty_query(request)
            return WebResponse.json(200, {"schema_version": 1, "status": "ok"})
        if request.path == "/api/v1/search":
            query = parse_search(request.query)
            return WebResponse.json(
                200,
                {"schema_version": 1, **search_payload(self._search.search(query))},
            )
        if request.path == "/api/v1/sources":
            page = parse_pagination(request.query)
            return WebResponse.json(
                200,
                {
                    "schema_version": 1,
                    **self._catalog.list_sources(limit=page.limit, offset=page.offset),
                },
            )
        if request.path == "/api/v1/research/sessions":
            page = parse_pagination(request.query)
            sessions = self._research_store.list_sessions(
                limit=page.limit, offset=page.offset
            )
            return WebResponse.json(
                200,
                {
                    "schema_version": 1,
                    "items": [
                        research_session_payload(session) for session in sessions
                    ],
                    "limit": page.limit,
                    "offset": page.offset,
                },
            )
        if request.path == "/api/v1/exports":
            page = parse_pagination(request.query)
            if page.offset != 0:
                raise RequestValidationError()
            return WebResponse.json(
                200,
                {"schema_version": 1, **self._exports.list_exports(limit=page.limit)},
            )
        session_match = _SESSION_ROUTE.fullmatch(request.path)
        if session_match is not None:
            _require_empty_query(request)
            session_id = validate_session_id(session_match.group(1))
            try:
                response = self._workflow.status(session_id)
            except ValueError as exc:
                raise JobNotFound() from exc
            return WebResponse.json(200, response.to_dict())
        job_match = _JOB_ROUTE.fullmatch(request.path)
        if job_match is not None:
            _require_empty_query(request)
            snapshot = self._job_reader.get(validate_job_id(job_match.group(1)))
            return WebResponse.json(
                200, {"schema_version": 1, "job": job_payload(snapshot)}
            )
        return _error(404, "not_found")

    def _post(self, request: WebRequest) -> WebResponse:
        _require_empty_query(request)
        if request.path == "/api/v1/sources/preview":
            source = self._require_source_acquisition()
            parsed_preview = parse_source_preview(request.body)
            return self._queued(
                "source_preview", lambda: source.preview(parsed_preview)
            )
        if request.path == "/api/v1/sources/acquire":
            source = self._require_source_acquisition()
            operation = source.prepare_acquisition(
                parse_source_acquisition(request.body)
            )
            return self._queued("source_acquisition", operation)
        if request.path == "/api/v1/research/sessions":
            parsed_start = parse_start_session(request.body)
            response = self._workflow.start(
                topic=parsed_start.topic,
                queries=parsed_start.queries,
                languages=parsed_start.languages,
                freshness_profile=parsed_start.freshness_profile,
            )
            return WebResponse.json(200, response.to_dict())
        match = _SESSION_ACTION_ROUTE.fullmatch(request.path)
        if match is None:
            return _error(404, "not_found")
        session_id = validate_session_id(match.group(1))
        action = match.group(2)
        if action == "decisions":
            parsed_decision = parse_decision(request.body)
            response = self._workflow.decide(
                session_id,
                expected_revision=parsed_decision.expected_revision,
                decision=parsed_decision.decision,
                idempotency_key=parsed_decision.idempotency_key,
            )
            return WebResponse.json(200, response.to_dict())
        if action == "discovery":
            parsed_revision = parse_revision(request.body)
            return self._queue_research(
                "research_discovery",
                lambda: self._workflow.discover(
                    session_id, expected_revision=parsed_revision.expected_revision
                ),
            )
        if action == "approvals":
            parsed_approval = parse_approval(request.body)
            response = self._workflow.approve(
                session_id,
                expected_revision=parsed_approval.expected_revision,
                video_ids=parsed_approval.video_ids,
                idempotency_key=parsed_approval.idempotency_key,
            )
            return WebResponse.json(200, response.to_dict())
        if action == "acquisition":
            parsed_acquisition = parse_acquisition(request.body)
            return self._queue_research(
                "research_acquisition",
                lambda: self._workflow.acquire(
                    session_id,
                    expected_revision=parsed_acquisition.expected_revision,
                    idempotency_key=parsed_acquisition.idempotency_key,
                    language=parsed_acquisition.language,
                ),
            )
        if action == "retry":
            parsed_retry = parse_retry(request.body)
            return self._queue_research(
                "research_retry",
                lambda: self._workflow.retry(
                    session_id,
                    expected_revision=parsed_retry.expected_revision,
                    idempotency_key=parsed_retry.idempotency_key,
                ),
            )
        parsed_export = parse_export(request.body)
        request_factory = self._export_request_factory
        if request_factory is None:
            return _error(503, "exports_unavailable")
        result = self._workflow.export(
            request_factory(session_id, parsed_export.force),
            package_version=self._package_version,
        )
        return WebResponse.json(
            200,
            {
                "schema_version": 1,
                "export": _export_payload(result),
            },
        )

    def _queue_research(
        self, kind: str, operation: Callable[[], ResearchResponse]
    ) -> WebResponse:
        return self._queued(kind, lambda: operation().to_dict())

    def _queued(self, kind: str, operation: JobOperation) -> WebResponse:
        snapshot = self._jobs.submit(kind, operation)
        return WebResponse.json(202, {"schema_version": 1, "job_id": snapshot.job_id})

    def _require_source_acquisition(self) -> SourceAcquisition:
        if self._source_acquisition is None:
            raise JobExecutorClosed()
        return self._source_acquisition


def _require_empty_query(request: WebRequest) -> None:
    if request.query:
        raise RequestValidationError()


def _export_payload(result: DossierExportResult) -> dict[str, object]:
    return safe_export_payload(
        Path(result.directory), result.manifest_sha256, result.dossier_sha256
    )


def _error(status: int, code: str) -> WebResponse:
    return WebResponse.json(
        status,
        {"schema_version": 1, "error": {"code": code}},
    )
