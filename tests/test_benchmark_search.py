from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import (
    DocumentRef,
    Passage,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)
from yt_insights.search.sqlite_fts import SQLiteFtsIndex

SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_search.py"


def _build_index(database: Path) -> None:
    document_id = compute_document_id("benchmark", "BenchVid_12", "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath="benchmark/transcripts/Benchmark [BenchVid_12].en.vtt",
        source_sha256="b" * 64,
        channel_id="benchmark",
        channel_title="Benchmark",
        video_id="BenchVid_12",
        video_title="Benchmark search",
        language="en",
    )
    text = "local retrieval benchmark"
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 1.0, 2.0, text),
        document_id=document_id,
        ordinal=0,
        start_seconds=1.0,
        end_seconds=2.0,
        text=text,
        youtube_url=youtube_url(document.video_id, 1.0),
    )
    SQLiteFtsIndex(database).rebuild(
        CorpusManifest(
            documents=(document,),
            passages=(passage,),
            invalid_sources=(),
            sources_discovered=1,
            sources_selected=1,
            sources_invalid=0,
        )
    )


def _run_script(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_nearest_rank_p95_is_explicit_and_replayable() -> None:
    from scripts.benchmark_search import nearest_rank_percentile

    assert nearest_rank_percentile([float(value) for value in range(1, 21)], 0.95) == 19.0


def test_cli_outputs_bounded_schema_without_query_text(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.txt"
    _build_index(database)
    queries.write_text("retrieval\nabsentterm\n", encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--warmup",
        "1",
        "--repeats",
        "2",
        "--limit",
        "1",
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "retrieval" not in result.stdout
    assert "absentterm" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload.keys() == {
        "database",
        "hit_counts",
        "latency_ms",
        "limit",
        "query_count",
        "repeats_per_query",
        "schema_version",
        "warmup_per_query",
    }
    assert payload["schema_version"] == 1
    assert payload["database"] == str(database.resolve())
    assert payload["query_count"] == 2
    assert payload["warmup_per_query"] == 1
    assert payload["repeats_per_query"] == 2
    assert payload["limit"] == 1
    assert payload["hit_counts"] == [1, 0]
    assert payload["latency_ms"].keys() == {"count", "max", "median", "min", "p95"}
    assert payload["latency_ms"]["count"] == 4
    assert 0 <= payload["latency_ms"]["min"] <= payload["latency_ms"]["median"]
    assert payload["latency_ms"]["median"] <= payload["latency_ms"]["p95"]
    assert payload["latency_ms"]["p95"] <= payload["latency_ms"]["max"]


@pytest.mark.parametrize(
    ("option", "value", "message"),
    (
        ("--warmup", "-1", "--warmup must be between 0 and 100"),
        ("--warmup", "101", "--warmup must be between 0 and 100"),
        ("--repeats", "0", "--repeats must be between 1 and 1000"),
        ("--repeats", "1001", "--repeats must be between 1 and 1000"),
        ("--limit", "0", "--limit must be between 1 and 20"),
        ("--limit", "21", "--limit must be between 1 and 20"),
    ),
)
def test_cli_rejects_out_of_bounds_values(
    tmp_path: Path, option: str, value: str, message: str
) -> None:
    database = tmp_path / "search.sqlite3"
    _build_index(database)

    result = _run_script("--database", str(database), option, value)

    assert result.returncode == 2
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_reports_missing_database_without_traceback(tmp_path: Path) -> None:
    result = _run_script("--database", str(tmp_path / "missing.sqlite3"))

    assert result.returncode == 2
    assert "benchmark error: search index does not exist" in result.stderr
    assert "Traceback" not in result.stderr
