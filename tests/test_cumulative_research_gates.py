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
