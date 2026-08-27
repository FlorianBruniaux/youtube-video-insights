#!/usr/bin/env python3
"""Fail-closed, replayable verification of the 50-source search slice.

The verifier is intentionally separate from production code.  It reads a
corpus, writes only a new caller-selected artifact directory outside that
corpus, and emits its JSON evidence before returning a non-zero status for any
failed critical gate.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import time
from typing import Any
from urllib.parse import parse_qs, urlparse


SOURCE_FILENAME_RE = re.compile(
    r"^(?:(?:\d{8}) - )?(?P<title>.+?) \[(?P<video_id>[A-Za-z0-9_-]{11})\]\.(?P<language>[A-Za-z0-9-]+)\.vtt$"
)
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
TIMESTAMP_RE = re.compile(r"^(?P<hours>\d{2,}):(?P<minutes>[0-5]\d):(?P<seconds>[0-5]\d)$")
FROZEN_QUERIES = (
    "artificial intelligence",
    "developer productivity",
    "software development",
    "security",
    "agents",
)
HOSTILE_QUERIES = (
    '"developer"',
    "ai-driven",
    "security:",
    "NEAR developer",
    "AI OR developer",
    "agent*",
    "(developer)",
    "don't",
    "design/engineering",
    r"back\end",
    "évaluation",
    '"machine learning"',
    "long-term",
    "title:developer",
    "NEAR/5 agent",
    "OR security",
    "deploy*",
    "(AI OR security)",
    "l'IA",
    "résumé",
)
CRITICAL_GATES = (
    "build_commands",
    "build_reports",
    "database_bytes",
    "frozen_results",
    "hostile_queries",
    "source_snapshot",
    "hit_validation",
    "primary_status",
    "worktree_status",
    "tests",
    "diff_checks",
)
_CLI_CACHE: dict[Path, Any] = {}


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float


def command_result_to_dict(result: CommandResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["command"] = list(result.command)
    return payload


def run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> CommandResult:
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        command=tuple(command),
        cwd=str(cwd),
        exit_code=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )


def prepare_artifact_dir(artifact_dir: Path, corpus_root: Path) -> Path:
    """Create one never-before-used artifact dir that is outside the corpus."""
    corpus = corpus_root.resolve()
    artifact = artifact_dir.resolve()
    if artifact == corpus or corpus in artifact.parents:
        raise ValueError("artifact directory is inside corpus")
    if artifact.exists():
        raise ValueError("artifact directory must not already exist")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.mkdir(mode=0o700)
    return artifact


def all_gates_pass(gates: dict[str, bool]) -> bool:
    """Require every named critical predicate to be present and true."""
    return bool(gates) and all(gates.values())


def source_snapshot(corpus_root: Path) -> list[dict[str, str]]:
    candidates = sorted(
        corpus_root.glob("**/transcripts/*.vtt"),
        key=lambda candidate: candidate.relative_to(corpus_root).as_posix(),
    )[:50]
    return [
        {
            "source": candidate.relative_to(corpus_root).as_posix(),
            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
        }
        for candidate in candidates
    ]


def snapshot_digest(snapshot: list[dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def parse_report(stdout: str) -> dict[str, int] | None:
    labels = {
        "Sources discovered": "sources_discovered",
        "Sources selected": "sources_selected",
        "Sources invalid": "sources_invalid",
        "Documents": "documents_indexed",
        "Passages": "passages_indexed",
    }
    report: dict[str, int] = {}
    for line in stdout.splitlines():
        label, separator, value = line.partition(": ")
        if separator and label in labels:
            try:
                report[labels[label]] = int(value)
            except ValueError:
                return None
    return report if set(report) == set(labels.values()) else None


def timestamp_seconds(value: object) -> int | None:
    if not isinstance(value, str):
        return None
    match = TIMESTAMP_RE.fullmatch(value)
    if match is None:
        return None
    return (
        int(match.group("hours")) * 3600
        + int(match.group("minutes")) * 60
        + int(match.group("seconds"))
    )


def validate_hit(hit: dict[str, Any], corpus_root: Path) -> list[str]:
    """Validate only the fields exposed by the existing search JSON contract."""
    errors: list[str] = []
    source = hit.get("source")
    if not isinstance(source, str):
        return ["source_missing"]
    source_path = PurePosixPath(source)
    if "\\" in source or source_path.is_absolute() or ".." in source_path.parts:
        errors.append("source_not_safe_relative")
    resolved_corpus = corpus_root.resolve()
    resolved_source = corpus_root / source
    try:
        resolved_source.resolve().relative_to(resolved_corpus)
    except (OSError, ValueError):
        errors.append("source_escapes_corpus")
    if not resolved_source.is_file():
        errors.append("source_not_existing_file")

    source_match = SOURCE_FILENAME_RE.fullmatch(source_path.name)
    if source_match is None:
        errors.append("source_filename_invalid")
        source_video_id = None
    else:
        source_video_id = source_match.group("video_id")

    seconds = timestamp_seconds(hit.get("timestamp"))
    if seconds is None:
        errors.append("timestamp_not_canonical_nonnegative")

    url = hit.get("url")
    if not isinstance(url, str):
        return [*errors, "url_missing"]
    parsed = urlparse(url)
    parameters = parse_qs(parsed.query, keep_blank_values=True)
    video_ids = parameters.get("v")
    time_values = parameters.get("t")
    if parsed.scheme != "https" or parsed.netloc != "youtube.com" or parsed.path != "/watch":
        errors.append("url_not_canonical_origin")
    if video_ids is None or len(video_ids) != 1 or VIDEO_ID_RE.fullmatch(video_ids[0]) is None:
        errors.append("url_video_id_invalid")
    elif source_video_id is not None and video_ids[0] != source_video_id:
        errors.append("source_video_id_mismatch")
    if (
        seconds is None
        or time_values is None
        or len(time_values) != 1
        or time_values[0] != f"{seconds}s"
    ):
        errors.append("url_timestamp_mismatch")
    elif (
        video_ids is not None
        and len(video_ids) == 1
        and url != f"https://youtube.com/watch?v={video_ids[0]}&t={seconds}s"
    ):
        errors.append("url_not_canonical")
    return errors


def find_primary_worktree(worktree: Path) -> tuple[Path | None, CommandResult]:
    result = run_command(["git", "worktree", "list", "--porcelain"], cwd=worktree)
    if result.exit_code != 0:
        return None, result
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line.removeprefix("worktree ")), result
    return None, result


def invoke_cli(worktree: Path, arguments: list[str]) -> CommandResult:
    """Invoke the real Click group from the supplied worktree without a shell."""
    resolved_worktree = worktree.resolve()
    cli = _CLI_CACHE.get(resolved_worktree)
    if cli is None:
        source_path = str(resolved_worktree / "src")
        sys.path.insert(0, source_path)
        for module_name in tuple(sys.modules):
            if module_name == "yt_insights" or module_name.startswith("yt_insights."):
                del sys.modules[module_name]
        cli = importlib.import_module("yt_insights.cli").cli
        _CLI_CACHE[resolved_worktree] = cli

    from click.testing import CliRunner

    started = time.perf_counter()
    result = CliRunner().invoke(cli, arguments)
    return CommandResult(
        command=("click-cli", *arguments),
        cwd=str(resolved_worktree),
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_ms=round((time.perf_counter() - started) * 1000, 3),
    )


def compact_search_result(
    query: str,
    result: CommandResult,
    corpus_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return serializable summary plus unpersisted hits and validation errors."""
    parsed: dict[str, Any] | None = None
    error: str | None = None
    if result.exit_code == 0:
        try:
            candidate = json.loads(result.stdout)
            if not isinstance(candidate, dict) or not isinstance(candidate.get("hits"), list):
                raise ValueError("JSON result misses hits list")
            if not all(isinstance(hit, dict) for hit in candidate["hits"]):
                raise ValueError("JSON result has non-object hit")
            parsed = candidate
        except (json.JSONDecodeError, ValueError) as exc:
            error = f"invalid_json:{exc}"
    else:
        error = (result.stderr or result.stdout).strip() or "nonzero_exit_without_output"
    hits: list[dict[str, Any]] = parsed["hits"] if parsed is not None else []
    validation_errors = [
        {"rank": hit.get("rank"), "errors": errors}
        for hit in hits
        if (errors := validate_hit(hit, corpus_root))
    ]
    return (
        {
            "query": query,
            "command": list(result.command),
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout_sha256": hashlib.sha256(result.stdout.encode("utf-8")).hexdigest(),
            "stderr": result.stderr,
            "hit_count": len(hits),
            "error": error,
            "validation_errors": validation_errors,
        },
        hits,
        validation_errors,
    )


def run_searches(
    worktree: Path,
    corpus_root: Path,
    database: Path,
    queries: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]]]:
    summaries: list[dict[str, Any]] = []
    hits_by_query: list[list[dict[str, Any]]] = []
    all_validation_errors: list[dict[str, Any]] = []
    for query in queries:
        command = invoke_cli(
            worktree,
            ["search", query, "--database", str(database), "--limit", "20", "--json"],
        )
        summary, hits, validation_errors = compact_search_result(query, command, corpus_root)
        summaries.append(summary)
        hits_by_query.append(hits)
        all_validation_errors.extend(validation_errors)
    return summaries, hits_by_query, all_validation_errors


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def verify(worktree: Path, corpus_root: Path, artifact_dir: Path) -> int:
    """Run all checks, always write `results.json`, and fail closed on gates."""
    if not worktree.is_dir():
        raise ValueError("worktree must be an existing directory")
    if not corpus_root.is_dir():
        raise ValueError("corpus must be an existing directory")
    artifact = prepare_artifact_dir(artifact_dir, corpus_root)
    database = artifact / "search-v1.sqlite3"
    results_path = artifact / "results.json"

    try:
        primary, primary_resolution = find_primary_worktree(worktree)
        primary_before = (
            run_command(["git", "status", "--porcelain=v1"], cwd=primary)
            if primary is not None
            else None
        )
        before = source_snapshot(corpus_root)

        build_arguments = [
            "index",
            "--corpus-root",
            str(corpus_root),
            "--database",
            str(database),
            "--limit",
            "50",
        ]
        build_one = invoke_cli(worktree, build_arguments)
        report_one = parse_report(build_one.stdout) if build_one.exit_code == 0 else None
        database_bytes_one = database.stat().st_size if database.is_file() else None
        frozen_one, frozen_one_hits, validation_one = run_searches(
            worktree, corpus_root, database, FROZEN_QUERIES
        )

        build_two = invoke_cli(worktree, build_arguments)
        report_two = parse_report(build_two.stdout) if build_two.exit_code == 0 else None
        database_bytes_two = database.stat().st_size if database.is_file() else None
        frozen_two, frozen_two_hits, validation_two = run_searches(
            worktree, corpus_root, database, FROZEN_QUERIES
        )
        hostile, _hostile_hits, validation_hostile = run_searches(
            worktree, corpus_root, database, HOSTILE_QUERIES
        )
        after = source_snapshot(corpus_root)

        test_environment = os.environ.copy()
        test_environment["PYTHONPATH"] = str(worktree / "src") + os.pathsep + test_environment.get("PYTHONPATH", "")
        tests = run_command([sys.executable, "-m", "pytest", "-q"], cwd=worktree, environment=test_environment)
        worktree_diff = run_command(["git", "diff", "--check"], cwd=worktree)
        primary_diff = (
            run_command(["git", "diff", "--check"], cwd=primary)
            if primary is not None
            else None
        )
        primary_after = (
            run_command(["git", "status", "--porcelain=v1"], cwd=primary)
            if primary is not None
            else None
        )
        worktree_final = run_command(["git", "status", "--porcelain=v1"], cwd=worktree)

        all_validation_errors = [*validation_one, *validation_two, *validation_hostile]
        frozen_equal = [
            first == second
            for first, second in zip(frozen_one_hits, frozen_two_hits, strict=True)
        ]
        gates = {
            "build_commands": build_one.exit_code == 0 and build_two.exit_code == 0,
            "build_reports": report_one is not None and report_one == report_two,
            "database_bytes": database_bytes_one is not None and database_bytes_one == database_bytes_two,
            "frozen_results": (
                len(frozen_one) == len(FROZEN_QUERIES)
                and len(frozen_two) == len(FROZEN_QUERIES)
                and all(item["exit_code"] == 0 and item["error"] is None for item in [*frozen_one, *frozen_two])
                and all(frozen_equal)
            ),
            "hostile_queries": (
                len(hostile) == len(HOSTILE_QUERIES)
                and all(item["exit_code"] == 0 and item["error"] is None for item in hostile)
            ),
            "source_snapshot": len(before) == 50 and before == after,
            "hit_validation": not all_validation_errors,
            "primary_status": (
                primary_before is not None
                and primary_after is not None
                and primary_before.exit_code == 0
                and primary_after.exit_code == 0
                and primary_before.stdout == primary_after.stdout
                and primary_before.stderr == primary_after.stderr
            ),
            "worktree_status": (
                worktree_final.exit_code == 0
                and worktree_final.stdout == ""
                and worktree_final.stderr == ""
            ),
            "tests": tests.exit_code == 0,
            "diff_checks": (
                worktree_diff.exit_code == 0
                and primary_diff is not None
                and primary_diff.exit_code == 0
            ),
        }
        payload: dict[str, Any] = {
            "worktree": str(worktree.resolve()),
            "corpus_root": str(corpus_root.resolve()),
            "artifact_dir": str(artifact),
            "database": str(database),
            "primary_worktree": str(primary) if primary is not None else None,
            "primary_resolution": command_result_to_dict(primary_resolution),
            "primary_status_before": command_result_to_dict(primary_before) if primary_before else None,
            "primary_status_after": command_result_to_dict(primary_after) if primary_after else None,
            "worktree_status_final": command_result_to_dict(worktree_final),
            "selected_source_snapshot_before": before,
            "selected_source_snapshot_after": after,
            "selected_source_snapshot_sha256_before": snapshot_digest(before),
            "selected_source_snapshot_sha256_after": snapshot_digest(after),
            "build_one": {**command_result_to_dict(build_one), "report": report_one, "database_bytes": database_bytes_one},
            "build_two": {**command_result_to_dict(build_two), "report": report_two, "database_bytes": database_bytes_two},
            "frozen_queries_after_build_one": frozen_one,
            "frozen_queries_after_build_two": frozen_two,
            "frozen_results_identical": frozen_equal,
            "hostile_queries": hostile,
            "hit_validation_error_count": len(all_validation_errors),
            "hit_validation_errors": all_validation_errors,
            "tests": command_result_to_dict(tests),
            "worktree_diff_check": command_result_to_dict(worktree_diff),
            "primary_diff_check": command_result_to_dict(primary_diff) if primary_diff else None,
            "gates": gates,
            "overall_pass": all_gates_pass(gates),
        }
    except Exception as error:
        payload = {
            "worktree": str(worktree),
            "corpus_root": str(corpus_root),
            "artifact_dir": str(artifact),
            "fatal_error": f"{type(error).__name__}: {error}",
            "gates": {name: False for name in CRITICAL_GATES},
            "overall_pass": False,
        }

    write_json(results_path, payload)
    return 0 if payload["overall_pass"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True, help="Clean worktree whose source is verified.")
    parser.add_argument("--corpus", type=Path, required=True, help="Read-only local VTT corpus root.")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        required=True,
        help="New, absent directory outside the corpus; never removed or reused.",
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        return verify(arguments.worktree, arguments.corpus, arguments.artifact_dir)
    except ValueError as error:
        print(f"verification precondition failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
