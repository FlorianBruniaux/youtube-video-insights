from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_agent_routing_fixture.py"
FIXTURE = REPOSITORY_ROOT / "tests" / "fixtures" / "agent-routing.json"
ALLOWED_EXPECTED = {
    "youtube-acquire",
    "youtube-research",
    "youtube-export",
    "none",
}


def run_checker(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def write_fixture(path: Path, rows: object) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False),
        encoding="utf-8",
    )


def valid_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for expected, count in (
        ("youtube-acquire", 10),
        ("youtube-research", 10),
        ("youtube-export", 10),
        ("none", 15),
    ):
        rows.extend(
            {
                "prompt": f"Unique fixture prompt {expected} {index}",
                "expected": expected,
            }
            for index in range(count)
        )
    return rows


def test_repository_routing_fixture_is_valid_and_balanced() -> None:
    result = run_checker(FIXTURE)

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary == {
        "counts": {
            "none": 15,
            "youtube-acquire": 10,
            "youtube-export": 10,
            "youtube-research": 10,
        },
        "status": "ok",
        "total": 45,
    }

    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 45
    assert {row["expected"] for row in rows} == ALLOWED_EXPECTED
    assert len({row["prompt"] for row in rows}) == 45


def test_checker_output_is_deterministic() -> None:
    first = run_checker(FIXTURE)
    second = run_checker(FIXTURE)

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == ""


def test_checker_rejects_invalid_utf8(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid-utf8.json"
    fixture.write_bytes(b"[\xff]")

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "UTF-8" in result.stderr


def test_checker_rejects_invalid_json(tmp_path: Path) -> None:
    fixture = tmp_path / "invalid.json"
    fixture.write_text("[", encoding="utf-8")

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "JSON" in result.stderr


@pytest.mark.parametrize(
    "invalid_rows",
    (
        {"prompt": "not a list", "expected": "none"},
        [None],
        [{"prompt": "missing label"}],
        [{"prompt": "", "expected": "none"}],
        [{"prompt": 42, "expected": "none"}],
        [{"prompt": "extra field", "expected": "none", "note": "no"}],
    ),
)
def test_checker_rejects_schema_violations(
    tmp_path: Path,
    invalid_rows: object,
) -> None:
    fixture = tmp_path / "invalid-schema.json"
    write_fixture(fixture, invalid_rows)

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "schema" in result.stderr.lower()


def test_checker_rejects_duplicate_prompts(tmp_path: Path) -> None:
    fixture = tmp_path / "duplicates.json"
    rows = valid_rows()
    rows[-1]["prompt"] = rows[0]["prompt"]
    write_fixture(fixture, rows)

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "duplicate" in result.stderr.lower()


def test_checker_rejects_unknown_labels(tmp_path: Path) -> None:
    fixture = tmp_path / "unknown-label.json"
    rows = valid_rows()
    rows[0]["expected"] = "youtube-seo"
    write_fixture(fixture, rows)

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "unknown" in result.stderr.lower()


@pytest.mark.parametrize(
    ("label", "remove_count"),
    (
        ("youtube-acquire", 1),
        ("youtube-research", 1),
        ("youtube-export", 1),
        ("none", 1),
    ),
)
def test_checker_rejects_counts_below_required_thresholds(
    tmp_path: Path,
    label: str,
    remove_count: int,
) -> None:
    fixture = tmp_path / "insufficient.json"
    rows = valid_rows()
    for _ in range(remove_count):
        rows.pop(next(index for index, row in enumerate(rows) if row["expected"] == label))
    write_fixture(fixture, rows)

    result = run_checker(fixture)

    assert result.returncode != 0
    assert result.stdout == ""
    assert "at least" in result.stderr.lower()
