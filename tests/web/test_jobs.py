from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Iterator

import pytest

from yt_insights.web.jobs import (
    JobExecutor,
    JobExecutorClosed,
    JobNotFound,
    JobQueueFull,
)


def iter_ids() -> Callable[[], str]:
    identifiers: Iterator[str] = iter(
        (
            "job-first",
            "job-second",
            "job-third",
            "job-fourth",
            "job-fifth",
        )
    )
    return identifiers.__next__


def wait_until(predicate: Callable[[], bool]) -> None:
    assert _wait_until(predicate), "operation did not reach the expected state"


def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def test_jobs_run_serially_and_publish_bounded_results() -> None:
    """Starting a second mutation before the first completes would break serialization."""
    started = threading.Event()
    release_first = threading.Event()
    observed: list[str] = []
    jobs = JobExecutor(max_queued=2, max_records=3, id_factory=iter_ids())

    def first_operation() -> dict[str, object]:
        observed.append("first-started")
        started.set()
        assert release_first.wait(timeout=1.0)
        observed.append("first-finished")
        return {"name": "first"}

    def second_operation() -> dict[str, object]:
        observed.append("second-started")
        return {"name": "second"}

    try:
        first = jobs.submit("discovery", first_operation)
        assert started.wait(timeout=1.0)
        second = jobs.submit("acquisition", second_operation)

        assert jobs.get(first.job_id).status == "running"
        assert jobs.get(second.job_id).status == "queued"
        assert observed == ["first-started"]

        release_first.set()
        wait_until(lambda: jobs.get(second.job_id).status == "succeeded")

        assert jobs.get(first.job_id).result == {"name": "first"}
        assert jobs.get(second.job_id).result == {"name": "second"}
        assert observed == ["first-started", "first-finished", "second-started"]
    finally:
        release_first.set()
        jobs.close()


def test_queue_full_does_not_start_or_evict_running_work() -> None:
    """Dropping a running mutation to make room would lose its durable outcome."""
    started = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())

    def block_forever() -> dict[str, object]:
        started.set()
        assert release.wait(timeout=1.0)
        return {}

    def second_operation() -> dict[str, object]:
        second_started.set()
        return {}

    try:
        running = jobs.submit("discovery", block_forever)
        assert started.wait(timeout=1.0)
        waiting = jobs.submit("acquisition", second_operation)

        with pytest.raises(JobQueueFull):
            jobs.submit("source_preview", lambda: {})

        assert jobs.get(running.job_id).status == "running"
        assert jobs.get(waiting.job_id).status == "queued"
        assert not second_started.is_set()

        release.set()
        wait_until(lambda: jobs.get(waiting.job_id).status == "succeeded")
    finally:
        release.set()
        jobs.close()


def test_failed_operations_are_not_retried_or_exposed() -> None:
    """Retrying a mutation or returning its exception can repeat or leak work."""
    attempts = 0
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())

    def fail_once() -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("/private/corpus.sqlite3")

    try:
        job = jobs.submit("acquisition", fail_once)
        wait_until(lambda: jobs.get(job.job_id).status == "failed")

        snapshot = jobs.get(job.job_id)
        assert attempts == 1
        assert snapshot.result is None
        assert snapshot.error_code == "operation_failed"
        assert "/private" not in repr(snapshot)
    finally:
        jobs.close()


def test_result_is_clipped_to_a_valid_24_kib_public_mapping() -> None:
    """A single oversized public payload must not make the job registry unbounded."""
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())

    try:
        job = jobs.submit("discovery", lambda: {"payload": "x" * (25 * 1024)})
        wait_until(lambda: jobs.get(job.job_id).status == "succeeded")

        result = jobs.get(job.job_id).result
        assert result == {"truncated": True}
        assert len(json.dumps(result, separators=(",", ":")).encode("utf-8")) <= 24 * 1024
    finally:
        jobs.close()


def test_only_terminal_records_are_evicted_in_completion_order() -> None:
    """Evicting a queued or running record would make an accepted job unobservable."""
    first_release = threading.Event()
    first_started = threading.Event()
    jobs = JobExecutor(max_queued=2, max_records=1, id_factory=iter_ids())

    def first_operation() -> dict[str, object]:
        first_started.set()
        assert first_release.wait(timeout=1.0)
        return {"order": 1}

    try:
        first = jobs.submit("discovery", first_operation)
        assert first_started.wait(timeout=1.0)
        second = jobs.submit("acquisition", lambda: {"order": 2})

        first_release.set()
        wait_until(lambda: jobs.get(second.job_id).status == "succeeded")

        with pytest.raises(JobNotFound):
            jobs.get(first.job_id)
        assert jobs.get(second.job_id).result == {"order": 2}
    finally:
        first_release.set()
        jobs.close()


def test_unknown_jobs_and_repeated_close_have_fixed_behavior() -> None:
    """A closed registry must neither disclose missing IDs nor accept new work."""
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())

    with pytest.raises(JobNotFound):
        jobs.get("unknown-job")

    jobs.close()
    jobs.close()

    with pytest.raises(JobExecutorClosed):
        jobs.submit("discovery", lambda: {})


def test_invalid_operation_output_becomes_a_fixed_failure() -> None:
    """Persisting a non-JSON result would violate the public job contract."""
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())

    try:
        job = jobs.submit("discovery", lambda: {"not_json": object()})
        wait_until(lambda: jobs.get(job.job_id).status == "failed")

        assert jobs.get(job.job_id).error_code == "operation_failed"
    finally:
        jobs.close()
