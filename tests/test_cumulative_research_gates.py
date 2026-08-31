from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "validate_cumulative_research_gates.py"
EVIDENCE = REPOSITORY_ROOT / "plans" / "evidence" / "2026-08-31-cumulative-research-gates.json"


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_mutated_evidence(tmp_path: Path, mutate) -> Path:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    mutate(payload)
    target = tmp_path / "gates.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _mark_all_external_gates_pass(payload: dict[str, object]) -> None:
    gates = payload["gates"]
    relevance = payload["relevance_pilot"]
    assert isinstance(gates, dict)
    assert isinstance(relevance, dict)
    gates.update(
        {
            "relevance_pilot": "PASS",
            "discovery_probe": "PASS",
            "refresh_performance": "PASS",
            "global_activation_ready": True,
        }
    )
    relevance.update(
        {
            "packet_status": "PASS",
            "observed_rank_1_to_5_result_count": 20,
            "observed_judgment_count": 20,
            "observed_null_judgment_count": 0,
            "observed_relevant_count": 16,
        }
    )


def test_accepts_checked_in_gate_evidence() -> None:
    result = _run(EVIDENCE)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "gate evidence valid\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update({"unexpected": True}), "unknown key"),
        (
            lambda payload: payload["corpus"].update({"unexpected": True}),
            "corpus has unknown key",
        ),
        (
            lambda payload: payload["gates"].update({"relevance_pilot": "BLOCKED"}),
            "gates.relevance_pilot",
        ),
        (
            lambda payload: payload["gates"].update({"relevance_pilot": []}),
            "gates.relevance_pilot must be a non-empty string",
        ),
        (
            lambda payload: payload["corpus"].update({"fingerprint": "not-a-hash"}),
            "corpus.fingerprint",
        ),
        (
            lambda payload: payload["corpus"].pop("fingerprint"),
            "corpus keys",
        ),
        (
            lambda payload: payload["refresh_performance"].update({"sample_count": 4}),
            "sample_count",
        ),
        (
            lambda payload: payload["discovery_probe"].update({"subjects": []}),
            "subjects",
        ),
        (
            lambda payload: payload["gates"].update({"global_activation_ready": True}),
            "global_activation_ready",
        ),
    ),
)
def test_rejects_invalid_gate_evidence(tmp_path: Path, mutate, message: str) -> None:
    result = _run(_write_mutated_evidence(tmp_path, mutate))

    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda payload: payload["refresh_performance"].update({"threshold_seconds": 61}),
            "threshold_seconds must equal 60",
        ),
        (
            lambda payload: payload["refresh_performance"].update(
                {"p95_wall_seconds": 60.000001, "incremental_refresh_required": True}
            ),
            "p95_wall_seconds",
        ),
        (
            lambda payload: payload["refresh_performance"].update({"incremental_refresh_required": True}),
            "incremental_refresh_required",
        ),
        (
            lambda payload: payload["discovery_probe"].update({"local_state_unchanged": False}),
            "local_state_unchanged",
        ),
        (
            lambda payload: payload["discovery_probe"]["subjects"][0].update({"exit_code": 1}),
            "exit_code",
        ),
        (
            lambda payload: payload["discovery_probe"]["subjects"][0].update({"candidate_count": 4}),
            "candidate_count",
        ),
    ),
)
def test_rejects_contradictory_all_pass_evidence(tmp_path: Path, mutate, message: str) -> None:
    def make_contradictory(payload: dict[str, object]) -> None:
        _mark_all_external_gates_pass(payload)
        mutate(payload)

    result = _run(_write_mutated_evidence(tmp_path, make_contradictory))

    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda payload: payload["refresh_performance"]["samples"][0].update({"exit_code": 1}),
            "exit_code",
        ),
        (
            lambda payload: payload["refresh_performance"]["samples"][0].update({"validation_exit_code": 1}),
            "validation_exit_code",
        ),
        (
            lambda payload: payload["relevance_pilot"].update({"packet_status": "UNKNOWN"}),
            "packet_status",
        ),
        (
            lambda payload: payload["relevance_pilot"].update({"observed_rank_1_to_5_result_count": 19}),
            "observed_rank_1_to_5_result_count",
        ),
        (
            lambda payload: payload["relevance_pilot"].update({"observed_judgment_count": 19}),
            "observed_judgment_count",
        ),
        (
            lambda payload: payload["relevance_pilot"].update({"observed_relevant_count": 15}),
            "observed_relevant_count",
        ),
        (
            lambda payload: payload["relevance_pilot"].update({"observed_null_judgment_count": 1}),
            "observed_null_judgment_count",
        ),
    ),
)
def test_rejects_all_pass_evidence_without_required_receipts(
    tmp_path: Path, mutate, message: str
) -> None:
    def make_contradictory(payload: dict[str, object]) -> None:
        _mark_all_external_gates_pass(payload)
        mutate(payload)

    result = _run(_write_mutated_evidence(tmp_path, make_contradictory))

    assert result.returncode == 2
    assert message in result.stderr
    assert result.stdout == ""
