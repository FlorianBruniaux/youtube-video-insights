"""Framework-neutral route orchestration for the versioned local API."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeVar, runtime_checkable

from yt_insights.catalog import CatalogError
from yt_insights.research.dossier import DossierExportRequest, DossierExportResult
from yt_insights.research.models import ResearchSession, ResearchState
from yt_insights.research.store import (
    DecisionReplayStatus,
    ResearchIdempotencyConflict,
    ResearchRevisionConflict,
    ResearchStore,
)
from yt_insights.research.workflow import ResearchResponse, ResearchWorkflow
from yt_insights.search.service import SearchService
from yt_insights.search.sqlite_fts import SearchIndexError

from .api import (
    PlanChanged,
    RequestValidationError,
    SourcePreviewRequest,
    StartSessionRequest,
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
_MAX_REPLAY_RECORDS = 100
_DECISION_STATES = frozenset({ResearchState.AWAITING_SUFFICIENCY})
_DISCOVERY_STATES = frozenset({ResearchState.DISCOVERING})
_APPROVAL_STATES = frozenset({ResearchState.AWAITING_CANDIDATES})
_ACQUISITION_STATES = frozenset({ResearchState.ACQUIRING})
_RETRY_STATES = frozenset(
    {
        ResearchState.FAILED_RETRYABLE,
        ResearchState.ACQUIRING,
        ResearchState.REINDEXING,
        ResearchState.ASSESSING,
        ResearchState.AWAITING_SUFFICIENCY,
    }
)
_EXPORT_STATES = frozenset(
    {
        ResearchState.AWAITING_SUFFICIENCY,
        ResearchState.AWAITING_CANDIDATES,
        ResearchState.COMPLETED,
    }
)
_T = TypeVar("_T")


class ResourceNotFound(Exception):
    """A requested public resource does not exist."""


class WorkflowConflict(Exception):
    """A valid request is incompatible with the current workflow state."""


class IdempotencyConflict(Exception):
    """A route-scoped idempotency key was reused with another payload."""


class ReplayRegistryFull(Exception):
    """No terminal replay record can be evicted safely."""


class JobSubmitter(Protocol):
    """The only queue capability used by mutation route helpers."""

    def submit(self, kind: str, operation: JobOperation) -> JobSnapshot: ...


@runtime_checkable
class JobReader(Protocol):
    def get(self, job_id: str) -> JobSnapshot: ...


class SourceAcquisition(Protocol):
    def preview(self, request: SourcePreviewRequest) -> Mapping[str, object]: ...

    def prepare_acquisition(
        self, fingerprint: str
    ) -> Callable[[], Mapping[str, object]]: ...


ExportRequestFactory = Callable[[str, bool], DossierExportRequest]


@dataclass(frozen=True, slots=True)
class _ReplayRecord:
    payload: object
    response: WebResponse
    job_id: str | None


class _ReplayRegistry:
    """Bound route-scoped HTTP replay while evicting only terminal records."""

    def __init__(self, *, maximum: int, jobs: JobReader) -> None:
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1:
            raise ValueError("replay capacity must be a positive integer")
        self._maximum = maximum
        self._jobs = jobs
        self._lock = threading.Lock()
        self._records: OrderedDict[tuple[str, str], _ReplayRecord] = OrderedDict()

    def run(
        self,
        *,
        route: str,
        key: str,
        payload: object,
        operation: Callable[[], WebResponse],
    ) -> WebResponse:
        identity = (route, key)
        with self._lock:
            prior = self._records.get(identity)
            if prior is not None:
                if prior.payload != payload:
                    raise IdempotencyConflict()
                return prior.response
            self._reserve_slot()
            response = operation()
            job_id = _response_job_id(response)
            self._records[identity] = _ReplayRecord(payload, response, job_id)
            return response

    def _reserve_slot(self) -> None:
        if len(self._records) < self._maximum:
            return
        for identity, record in tuple(self._records.items()):
            if self._terminal(record):
                del self._records[identity]
                return
        raise ReplayRegistryFull()

    def _terminal(self, record: _ReplayRecord) -> bool:
        if record.job_id is None:
            return True
        try:
            snapshot = self._jobs.get(record.job_id)
        except JobNotFound:
            return True
        return snapshot.status in {"succeeded", "failed"}


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
        max_replay_records: int = _MAX_REPLAY_RECORDS,
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
        self._replays = _ReplayRegistry(
            maximum=max_replay_records,
            jobs=resolved_reader,
        )

    def handle(self, request: WebRequest) -> WebResponse:
        """Return a fixed public response without reflecting exception text."""
        try:
            return self._dispatch(request)
        except RequestValidationError:
            return _error(400, "invalid_request")
        except (JobNotFound, ResourceNotFound):
            return _error(404, "not_found")
        except PlanChanged:
            return _error(409, "plan_changed")
        except ResearchRevisionConflict:
            return _error(409, "stale_revision")
        except WorkflowConflict:
            return _error(409, "workflow_conflict")
        except IdempotencyConflict:
            return _error(409, "idempotency_conflict")
        except (JobQueueFull, ReplayRegistryFull):
            return _error(429, "job_queue_full")
        except JobExecutorClosed:
            return _error(503, "jobs_unavailable")
        except SearchIndexError:
            return _error(503, "search_unavailable")
        except CatalogError:
            return _error(503, "catalog_unavailable")
        except sqlite3.Error:
            return _error(503, "research_unavailable")
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
            self._require_session(session_id)
            response = self._workflow.status(session_id)
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
            parsed_source_acquisition = parse_source_acquisition(request.body)
            return self._replays.run(
                route=request.path,
                key=parsed_source_acquisition.idempotency_key,
                payload=("fingerprint", parsed_source_acquisition.fingerprint),
                operation=lambda: self._admit_source_acquisition(
                    source, parsed_source_acquisition.fingerprint
                ),
            )
        if request.path == "/api/v1/research/sessions":
            parsed_start = parse_start_session(request.body)
            return self._replays.run(
                route=request.path,
                key=parsed_start.idempotency_key,
                payload=(
                    parsed_start.topic,
                    parsed_start.queries,
                    parsed_start.languages,
                    parsed_start.freshness_profile.value,
                ),
                operation=lambda: self._start_session(parsed_start),
            )
        match = _SESSION_ACTION_ROUTE.fullmatch(request.path)
        if match is None:
            return _error(404, "not_found")
        session_id = validate_session_id(match.group(1))
        action = match.group(2)
        if action == "decisions":
            parsed_decision = parse_decision(request.body)
            response = self._synchronous_research_operation(
                session_id,
                expected_revision=parsed_decision.expected_revision,
                compatible_states=_DECISION_STATES,
                operation=lambda: self._workflow.decide(
                    session_id,
                    expected_revision=parsed_decision.expected_revision,
                    decision=parsed_decision.decision,
                    idempotency_key=parsed_decision.idempotency_key,
                ),
            )
            return WebResponse.json(200, response.to_dict())
        if action == "discovery":
            parsed_revision = parse_revision(request.body)
            return self._queue_research(
                "research_discovery",
                session_id,
                parsed_revision.expected_revision,
                _DISCOVERY_STATES,
                lambda: self._workflow.discover(
                    session_id, expected_revision=parsed_revision.expected_revision
                ),
            )
        if action == "approvals":
            parsed_approval = parse_approval(request.body)
            response = self._synchronous_research_operation(
                session_id,
                expected_revision=parsed_approval.expected_revision,
                compatible_states=_APPROVAL_STATES,
                operation=lambda: self._workflow.approve(
                    session_id,
                    expected_revision=parsed_approval.expected_revision,
                    video_ids=parsed_approval.video_ids,
                    idempotency_key=parsed_approval.idempotency_key,
                ),
            )
            return WebResponse.json(200, response.to_dict())
        if action == "acquisition":
            parsed_acquisition = parse_acquisition(request.body)
            return self._queue_research(
                "research_acquisition",
                session_id,
                parsed_acquisition.expected_revision,
                _ACQUISITION_STATES,
                lambda: self._workflow.acquire(
                    session_id,
                    expected_revision=parsed_acquisition.expected_revision,
                    idempotency_key=parsed_acquisition.idempotency_key,
                    language=parsed_acquisition.language,
                ),
                replay=lambda: self._is_acquisition_replay(
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
                session_id,
                parsed_retry.expected_revision,
                _RETRY_STATES,
                lambda: self._workflow.retry(
                    session_id,
                    expected_revision=parsed_retry.expected_revision,
                    idempotency_key=parsed_retry.idempotency_key,
                ),
                replay=lambda: self._is_retry_replay(
                    session_id,
                    expected_revision=parsed_retry.expected_revision,
                    idempotency_key=parsed_retry.idempotency_key,
                ),
            )
        parsed_export = parse_export(request.body)
        request_factory = self._export_request_factory
        if request_factory is None:
            return _error(503, "exports_unavailable")
        result = self._synchronous_research_operation(
            session_id,
            expected_revision=None,
            compatible_states=_EXPORT_STATES,
            operation=lambda: self._workflow.export(
                request_factory(session_id, parsed_export.force),
                package_version=self._package_version,
            ),
        )
        return WebResponse.json(
            200,
            {
                "schema_version": 1,
                "export": _export_payload(result),
            },
        )

    def _queue_research(
        self,
        kind: str,
        session_id: str,
        expected_revision: int,
        compatible_states: frozenset[ResearchState],
        operation: Callable[[], ResearchResponse],
        replay: Callable[[], bool] | None = None,
    ) -> WebResponse:
        self._require_session_state_or_replay(
            session_id,
            expected_revision=expected_revision,
            compatible_states=compatible_states,
            replay=replay,
        )

        def guarded_operation() -> Mapping[str, object]:
            try:
                self._require_session_state_or_replay(
                    session_id,
                    expected_revision=expected_revision,
                    compatible_states=compatible_states,
                    replay=replay,
                )
            except ResearchRevisionConflict:
                return _job_error("stale_revision")
            except ResourceNotFound:
                return _job_error("not_found")
            except WorkflowConflict:
                return _job_error("workflow_conflict")

            try:
                return operation().to_dict()
            except ResearchRevisionConflict:
                return _job_error("stale_revision")
            except ResourceNotFound:
                return _job_error("not_found")
            except WorkflowConflict:
                return _job_error("workflow_conflict")
            except ValueError:
                return self._queued_workflow_value_error(
                    session_id,
                    expected_revision=expected_revision,
                    compatible_states=compatible_states,
                )

        return self._queued(kind, guarded_operation)

    def _require_session_state_or_replay(
        self,
        session_id: str,
        *,
        expected_revision: int,
        compatible_states: frozenset[ResearchState],
        replay: Callable[[], bool] | None,
    ) -> None:
        try:
            self._require_session_state(
                session_id,
                expected_revision=expected_revision,
                compatible_states=compatible_states,
            )
        except (ResearchRevisionConflict, WorkflowConflict):
            if replay is None or not replay():
                raise

    def _is_acquisition_replay(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        language: str,
    ) -> bool:
        try:
            attempt = self._research_store.get_acquisition_replay(
                session_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                language=language,
                cookies_from_browser=None,
            )
        except ResearchIdempotencyConflict:
            return False
        return attempt is not None and attempt.status in {
            "running",
            "failed_retryable",
            "completed",
        }

    def _is_retry_replay(
        self,
        session_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> bool:
        try:
            replay = self._research_store.get_decision_replay(
                session_id,
                expected_revision=expected_revision,
                action="retry",
                request={"expected_revision": expected_revision},
                idempotency_key=idempotency_key,
            )
        except ResearchIdempotencyConflict:
            return False
        return replay is not None and replay.status in {
            DecisionReplayStatus.COMPLETED,
            DecisionReplayStatus.RETRY_IN_PROGRESS,
        }

    def _synchronous_research_operation(
        self,
        session_id: str,
        *,
        expected_revision: int | None,
        compatible_states: frozenset[ResearchState],
        operation: Callable[[], _T],
    ) -> _T:
        self._require_session(session_id)
        try:
            return operation()
        except ResearchRevisionConflict:
            raise
        except ValueError as exc:
            code = self._workflow_value_error_code(
                session_id,
                expected_revision=expected_revision,
                compatible_states=compatible_states,
            )
            if code == "stale_revision":
                raise ResearchRevisionConflict() from exc
            if code == "workflow_conflict":
                raise WorkflowConflict() from exc
            raise

    def _queued_workflow_value_error(
        self,
        session_id: str,
        *,
        expected_revision: int,
        compatible_states: frozenset[ResearchState],
    ) -> Mapping[str, object]:
        try:
            code = self._workflow_value_error_code(
                session_id,
                expected_revision=expected_revision,
                compatible_states=compatible_states,
            )
        except ResourceNotFound:
            return _job_error("not_found")
        except (ValueError, sqlite3.Error):
            return _job_error("operation_failed")
        return _job_error(code or "operation_failed")

    def _workflow_value_error_code(
        self,
        session_id: str,
        *,
        expected_revision: int | None,
        compatible_states: frozenset[ResearchState],
    ) -> str | None:
        session = self._require_session(session_id)
        if expected_revision is not None and session.revision != expected_revision:
            return "stale_revision"
        if session.state not in compatible_states:
            return "workflow_conflict"
        return None

    def _require_session_state(
        self,
        session_id: str,
        *,
        expected_revision: int,
        compatible_states: frozenset[ResearchState],
    ) -> None:
        session = self._require_session_revision(
            session_id,
            expected_revision=expected_revision,
        )
        if session.state not in compatible_states:
            raise WorkflowConflict()

    def _require_session_revision(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> ResearchSession:
        session = self._require_session(session_id)
        if session.revision != expected_revision:
            raise ResearchRevisionConflict()
        return session

    def _require_session(self, session_id: str) -> ResearchSession:
        try:
            return self._research_store.get_session(session_id)
        except ValueError as exc:
            if type(exc) is ValueError and exc.args == ("session does not exist",):
                raise ResourceNotFound() from exc
            raise

    def _start_session(self, request: StartSessionRequest) -> WebResponse:
        response = self._workflow.start(
            topic=request.topic,
            queries=request.queries,
            languages=request.languages,
            freshness_profile=request.freshness_profile,
        )
        return WebResponse.json(200, response.to_dict())

    def _admit_source_acquisition(
        self, source: SourceAcquisition, fingerprint: str
    ) -> WebResponse:
        operation = source.prepare_acquisition(fingerprint)
        return self._queued("source_acquisition", operation)

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


def _response_job_id(response: WebResponse) -> str | None:
    if response.status != 202:
        return None
    job_id = response.json_body.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("queued response has no job identifier")
    return job_id


def _job_error(code: str) -> dict[str, object]:
    return {"schema_version": 1, "error": {"code": code}}


def _error(status: int, code: str) -> WebResponse:
    return WebResponse.json(
        status,
        {"schema_version": 1, "error": {"code": code}},
    )
