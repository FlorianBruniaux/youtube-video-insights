"""Bounded, process-local serialization for state-changing web operations."""

from __future__ import annotations

import json
import secrets
import threading
from collections import deque
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final, Literal

_DEFAULT_MAX_QUEUED: Final = 32
_DEFAULT_MAX_RECORDS: Final = 100
_MAX_RESULT_BYTES: Final = 24 * 1024
_ERROR_OPERATION_FAILED: Final = "operation_failed"

JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobOperation = Callable[[], Mapping[str, object]]


class JobQueueFull(Exception):
    """Raised when accepting work would exceed the bounded queue."""


class JobNotFound(Exception):
    """Raised when a job is unknown or its terminal record was evicted."""


class JobExecutorClosed(Exception):
    """Raised when submitting work after executor shutdown."""


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """One path-free public view of a process-local job."""

    job_id: str
    kind: str
    status: JobStatus
    result: Mapping[str, object] | None = None
    error_code: str | None = None


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    kind: str
    status: JobStatus
    result: dict[str, object] | None = None
    error_code: str | None = None


class JobExecutor:
    """Run accepted mutations sequentially while retaining bounded public state."""

    def __init__(
        self,
        *,
        max_queued: int = _DEFAULT_MAX_QUEUED,
        max_records: int = _DEFAULT_MAX_RECORDS,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if isinstance(max_queued, bool) or not isinstance(max_queued, int) or max_queued < 0:
            raise ValueError("max_queued must be a non-negative integer")
        if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records < 1:
            raise ValueError("max_records must be a positive integer")
        self._max_records = max_records
        self._id_factory = id_factory or _new_job_id
        self._lock = threading.Lock()
        # A reservation covers both the one worker and all permitted waiting jobs.
        self._capacity = threading.BoundedSemaphore(max_queued + 1)
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="yt-insights-web"
        )
        self._records: dict[str, _JobRecord] = {}
        self._terminal_ids: deque[str] = deque()
        self._closed = False

    def submit(self, kind: str, operation: JobOperation) -> JobSnapshot:
        """Reserve capacity and schedule one operation without retrying it."""
        if not isinstance(kind, str) or not kind:
            raise ValueError("kind must be a non-empty string")
        if not callable(operation):
            raise TypeError("operation must be callable")

        with self._lock:
            if self._closed:
                raise JobExecutorClosed()
            if not self._capacity.acquire(blocking=False):
                raise JobQueueFull()
            job_id: str | None = None
            try:
                job_id = self._new_unique_id_locked()
                record = _JobRecord(job_id=job_id, kind=kind, status="queued")
                self._records[job_id] = record
                self._executor.submit(self._run, job_id, operation)
            except Exception:
                if job_id is not None:
                    self._records.pop(job_id, None)
                self._capacity.release()
                raise
            return self._snapshot(record)

    def get(self, job_id: str) -> JobSnapshot:
        """Return a fresh immutable snapshot or disclose no stale registry detail."""
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFound()
            return self._snapshot(record)

    def close(self) -> None:
        """Stop accepting jobs while allowing already accepted work to drain once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _new_unique_id_locked(self) -> str:
        job_id = self._id_factory()
        if not isinstance(job_id, str) or not job_id:
            raise ValueError("job id factory must return a non-empty string")
        if job_id in self._records:
            raise ValueError("job id factory returned a duplicate id")
        return job_id

    def _run(self, job_id: str, operation: JobOperation) -> None:
        with self._lock:
            record = self._records[job_id]
            record.status = "running"
        try:
            result = _bounded_result(operation())
        except Exception:
            status: JobStatus = "failed"
            public_result: dict[str, object] | None = None
            error_code: str | None = _ERROR_OPERATION_FAILED
        else:
            status = "succeeded"
            public_result = result
            error_code = None
        finally:
            with self._lock:
                completed_record = self._records.get(job_id)
                if completed_record is not None:
                    completed_record.status = status
                    completed_record.result = public_result
                    completed_record.error_code = error_code
                    self._terminal_ids.append(job_id)
                    self._evict_terminal_records_locked()
                self._capacity.release()

    def _evict_terminal_records_locked(self) -> None:
        while len(self._records) > self._max_records and self._terminal_ids:
            job_id = self._terminal_ids.popleft()
            self._records.pop(job_id, None)

    @staticmethod
    def _snapshot(record: _JobRecord) -> JobSnapshot:
        result = None if record.result is None else _copy_public_mapping(record.result)
        return JobSnapshot(
            job_id=record.job_id,
            kind=record.kind,
            status=record.status,
            result=result,
            error_code=record.error_code,
        )


def _new_job_id() -> str:
    return secrets.token_urlsafe(24)


def _bounded_result(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError("operation result must be a mapping")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_RESULT_BYTES:
        return {"truncated": True}
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise TypeError("operation result must be a JSON object")
    return decoded


def _copy_public_mapping(value: Mapping[str, object]) -> dict[str, object]:
    copied = json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    if not isinstance(copied, dict):
        raise TypeError("stored result must be a JSON object")
    return copied
