from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

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


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare_search_relevance_evaluation.py"
COMMIT_SHA = "0123456789abcdef0123456789abcdef01234567"
CODE_FILES = {
    "scripts/prepare_search_relevance_evaluation.py",
    "src/yt_insights/__init__.py",
    "src/yt_insights/vtt_parser.py",
    "src/yt_insights/search/__init__.py",
    "src/yt_insights/search/chunker.py",
    "src/yt_insights/search/corpus.py",
    "src/yt_insights/search/models.py",
    "src/yt_insights/search/query.py",
    "src/yt_insights/search/service.py",
    "src/yt_insights/search/sqlite_fts.py",
}


def _build_index(database: Path) -> tuple[DocumentRef, Passage]:
    document_id = compute_document_id("evaluation-channel", "EvalVideo12", "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath="evaluation-channel/transcripts/Evaluation [EvalVideo12].en.vtt",
        source_sha256="a" * 64,
        channel_id="evaluation-channel",
        channel_title="Evaluation Channel",
        video_id="EvalVideo12",
        video_title="Reliable local retrieval",
        language="en",
    )
    text = "reliable local retrieval preserves timestamped provenance"
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 12.25, 18.5, text),
        document_id=document_id,
        ordinal=0,
        start_seconds=12.25,
        end_seconds=18.5,
        text=text,
        youtube_url=youtube_url(document.video_id, 12.25),
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
    return document, passage


def _query_payload(*queries: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "UNKNOWN",
        "instructions": ["Populate judgments only after human review."],
        "pilot": {
            "required_distinct_subjects": 3,
            "required_query_count": 4,
            "results_per_query": 5,
            "required_result_judgment_count": 20,
            "minimum_relevant_result_count_for_pilot_pass": 16,
            "relevant_grades": [1, 2],
            "note": "A pilot pass is not a release pass.",
        },
        "release": {
            "minimum_query_case_count": 60,
            "maximum_query_case_count": 100,
            "required_categories": [
                "exact",
                "natural_question",
                "paraphrase",
                "bilingual",
                "filter",
                "hostile",
                "no_answer",
            ],
            "required_metrics": ["Recall@5", "MRR@10", "nDCG@10", "zero-result rate"],
        },
        "queries": list(queries),
    }


def _query(
    *,
    query_id: str = "pilot-s1-q1",
    query: str = "local retrieval",
    phase: str = "pilot",
    subject_id: str = "subject-1",
    category: str = "exact",
    query_language: str = "en",
    channel: str | None = None,
    language: str | None = "en",
) -> dict[str, object]:
    return {
        "id": query_id,
        "phase": phase,
        "subject_id": subject_id,
        "label": "Local retrieval",
        "query": query,
        "category": category,
        "query_language": query_language,
        "filters": {"channel": channel, "language": language},
    }


def _pilot_cases() -> tuple[dict[str, object], ...]:
    return (
        _query(query_id="pilot-s1-q1", query="local retrieval", subject_id="subject-1"),
        _query(
            query_id="pilot-s2-q1",
            query="timestamped provenance",
            subject_id="subject-2",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q1",
            query="reliable",
            subject_id="subject-3",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q2",
            query="preserves",
            subject_id="subject-3",
            language=None,
        ),
    )


def _release_cases(
    count: int = 60, categories: tuple[str, ...] | None = None
) -> tuple[dict[str, object], ...]:
    required_categories = categories or (
        "exact",
        "natural_question",
        "paraphrase",
        "bilingual",
        "filter",
        "hostile",
        "no_answer",
    )
    return tuple(
        _query(
            query_id=f"release-query-{index:03d}",
            query=f"release topic {index}",
            phase="release",
            subject_id=f"release-subject-{index:03d}",
            category=required_categories[(index - 1) % len(required_categories)],
            query_language="en",
            language=None,
        )
        for index in range(1, count + 1)
    )


def _write_queries(path: Path, *queries: dict[str, object]) -> None:
    path.write_text(
        json.dumps(_query_payload(*queries), ensure_ascii=False),
        encoding="utf-8",
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


def _run_executable(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    environment["PATH"] = os.pathsep.join(
        (str(Path(sys.executable).parent), environment.get("PATH", ""))
    )
    return subprocess.run(
        [str(SCRIPT), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def test_cli_writes_byte_identical_packets_with_complete_empty_judgments(
    tmp_path: Path,
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    first_output = tmp_path / "first.json"
    second_output = tmp_path / "second.json"
    document, passage = _build_index(database)
    _write_queries(queries, *_pilot_cases())

    common_arguments = (
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        "5",
    )
    first = _run_script(*common_arguments, "--output", str(first_output))
    second = _run_script(*common_arguments, "--output", str(second_output))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stderr == second.stderr == ""
    assert first_output.read_bytes() == second_output.read_bytes()

    packet = json.loads(first_output.read_text(encoding="utf-8"))
    assert packet["schema_version"] == 1
    assert packet["commit_sha"] == COMMIT_SHA
    assert packet["top_k"] == 5
    assert packet["code"].keys() == {"files", "sha256"}
    assert set(packet["code"]["files"]) == CODE_FILES
    assert all(
        len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        for digest in packet["code"]["files"].values()
    )
    assert packet["code"]["files"]["scripts/prepare_search_relevance_evaluation.py"] == (
        hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    )
    assert packet["evaluation"] == {
        "decision": None,
        "method": None,
        "notes": None,
        "reviewed_at": None,
        "reviewer": None,
        "status": "UNKNOWN",
        "threshold": None,
    }
    assert packet["queries"][0]["id"] == "pilot-s1-q1"
    assert packet["index"]["sha256"] == hashlib.sha256(database.read_bytes()).hexdigest()
    result = packet["queries"][0]["results"][0]
    assert result == {
        "channel_id": document.channel_id,
        "channel_title": document.channel_title,
        "document_id": document.document_id,
        "end_seconds": passage.end_seconds,
        "excerpt": passage.text,
        "judgment": {
            "notes": None,
            "relevance": None,
            "reviewed_at": None,
            "reviewer": None,
        },
        "language": document.language,
        "ordinal": passage.ordinal,
        "passage_id": passage.passage_id,
        "rank": 1,
        "source_relpath": document.source_relpath,
        "source_sha256": document.source_sha256,
        "start_seconds": passage.start_seconds,
        "text": passage.text,
        "video_id": document.video_id,
        "video_title": document.video_title,
        "youtube_url": passage.youtube_url,
    }


def test_cli_preserves_the_explicit_query_order(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _write_queries(
        queries,
        _query(query_id="pilot-s1-q1", query="local retrieval"),
        _query(
            query_id="pilot-s2-q1",
            query="absent term",
            subject_id="subject-2",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q1",
            query="reliable",
            subject_id="subject-3",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q2",
            query="preserves",
            subject_id="subject-3",
            language=None,
        ),
    )

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 0, result.stderr
    packet = json.loads(output.read_text(encoding="utf-8"))
    assert [query["id"] for query in packet["queries"]] == [
        "pilot-s1-q1",
        "pilot-s2-q1",
        "pilot-s3-q1",
        "pilot-s3-q2",
    ]
    assert packet["queries"][1]["results"] == []


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update(queries=[]), "queries must be a non-empty JSON array"),
        (
            lambda payload: payload["queries"].append(dict(payload["queries"][0])),
            "duplicate query id",
        ),
        (
            lambda payload: payload["queries"].append(
                {**payload["queries"][0], "id": "different-id", "query": "local---retrieval"}
            ),
            "duplicate retrieval query",
        ),
        (
            lambda payload: payload["queries"][0]["filters"].update(after="2026-01-01"),
            "filters must contain exactly channel and language",
        ),
        (
            lambda payload: payload["queries"][0].update(
                query="[REPLACE_WITH_REAL_QUERY_1]"
            ),
            "placeholder",
        ),
        (
            lambda payload: payload["pilot"].update({"[REPLACE_WITH_COUNT]": 4}),
            "placeholder",
        ),
    ),
)
def test_cli_rejects_hostile_query_sets(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    payload = _query_payload(*_pilot_cases())
    mutate(payload)  # type: ignore[operator]
    queries.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_cli_rejects_invalid_json_and_a_missing_query_file(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    invalid_queries = tmp_path / "invalid.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    invalid_queries.write_text("{", encoding="utf-8")

    invalid = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(invalid_queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )
    missing = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(tmp_path / "missing.json"),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert invalid.returncode == 2
    assert "queries file must contain valid JSON" in invalid.stderr
    assert missing.returncode == 2
    assert "input path cannot be resolved" in missing.stderr
    assert not output.exists()


def test_cli_rejects_duplicate_json_object_keys(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    serialized = json.dumps(_query_payload(*_pilot_cases()))
    queries.write_text(
        serialized.replace('"status":', '"schema_version": 1, "status":', 1),
        encoding="utf-8",
    )

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert "duplicate JSON object key" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (lambda payload: payload.update(status="PASS"), "status must equal UNKNOWN"),
        (
            lambda payload: payload["pilot"].update(review={"decision": "PASS"}),
            "pilot must contain exactly",
        ),
        (
            lambda payload: payload["pilot"].update(required_query_count=0),
            "pilot metadata constants must match the tracked template",
        ),
        (
            lambda payload: payload["release"].update(judgment="PASS"),
            "release must contain exactly",
        ),
        (
            lambda payload: payload["release"].update(
                minimum_query_case_count=101, maximum_query_case_count=100
            ),
            "release metadata constants must match the tracked template",
        ),
    ),
)
def test_cli_rejects_unreviewed_contract_injection(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    payload = _query_payload(*_pilot_cases())
    mutate(payload)  # type: ignore[operator]
    queries.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    (
        (
            "pilot",
            "required_distinct_subjects",
            4,
            "pilot metadata constants must match the tracked template",
        ),
        (
            "pilot",
            "minimum_relevant_result_count_for_pilot_pass",
            15,
            "pilot metadata constants must match the tracked template",
        ),
        (
            "pilot",
            "relevant_grades",
            [True, 2],
            "pilot metadata constants must match the tracked template",
        ),
        (
            "release",
            "minimum_query_case_count",
            59,
            "release metadata constants must match the tracked template",
        ),
        (
            "release",
            "maximum_query_case_count",
            101,
            "release metadata constants must match the tracked template",
        ),
    ),
)
def test_cli_requires_exact_tracked_metadata_constants(
    tmp_path: Path, section: str, field: str, value: object, message: str
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    payload = _query_payload(*_pilot_cases())
    payload[section][field] = value  # type: ignore[index]
    queries.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    ("mutate", "message"),
    (
        (
            lambda queries: queries[0].update(phase="review"),
            "phase must be pilot or release",
        ),
        (
            lambda queries: queries[0].update(category="unknown"),
            "category is unsupported",
        ),
        (
            lambda queries: queries[0].update(query_language="english_US"),
            "query_language must be a conservative BCP47 tag",
        ),
        (
            lambda queries: queries.pop(),
            "exactly 4 pilot queries",
        ),
        (
            lambda queries: [query.update(subject_id="same-subject") for query in queries],
            "at least 3 distinct pilot subjects",
        ),
    ),
)
def test_cli_validates_actual_pilot_cases(
    tmp_path: Path, mutate: object, message: str
) -> None:
    database = tmp_path / "search.sqlite3"
    query_file = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    queries = list(_pilot_cases())
    mutate(queries)  # type: ignore[operator]
    query_file.write_text(json.dumps(_query_payload(*queries)), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(query_file),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert not output.exists()


def test_cli_requires_release_volume_category_coverage_and_top_k_ten(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    _build_index(database)
    incomplete_release_file = tmp_path / "incomplete-release.json"
    complete_release_file = tmp_path / "complete-release.json"
    incomplete_output = tmp_path / "incomplete.json"
    wrong_top_k_output = tmp_path / "wrong-top-k.json"
    complete_output = tmp_path / "complete.json"
    incomplete_release_file.write_text(
        json.dumps(
            _query_payload(*_pilot_cases(), *_release_cases(categories=("exact",)))
        ),
        encoding="utf-8",
    )
    complete_release_file.write_text(
        json.dumps(_query_payload(*_pilot_cases(), *_release_cases())),
        encoding="utf-8",
    )

    incomplete = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(incomplete_release_file),
        "--output",
        str(incomplete_output),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        "10",
    )
    wrong_top_k = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(complete_release_file),
        "--output",
        str(wrong_top_k_output),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        "5",
    )
    complete = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(complete_release_file),
        "--output",
        str(complete_output),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        "10",
    )

    assert incomplete.returncode == 2
    assert "release queries must cover every required category" in incomplete.stderr
    assert wrong_top_k.returncode == 2
    assert "--top-k must equal 10 when release queries are present" in wrong_top_k.stderr
    assert complete.returncode == 0, complete.stderr
    assert len(json.loads(complete_output.read_text(encoding="utf-8"))["queries"]) == 64


def test_cli_requires_top_k_at_least_five_for_pilot(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    _build_index(database)
    _write_queries(queries, *_pilot_cases())
    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(tmp_path / "packet.json"),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        "4",
    )

    assert result.returncode == 2
    assert "--top-k must be at least 5 for pilot queries" in result.stderr


def test_duplicate_detection_uses_fts_expression_and_exact_filters(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    duplicate_queries = tmp_path / "duplicate-queries.json"
    exact_filter_queries = tmp_path / "exact-filter-queries.json"
    duplicate_output = tmp_path / "duplicate-packet.json"
    exact_filter_output = tmp_path / "exact-filter-packet.json"
    _build_index(database)
    _write_queries(
        duplicate_queries,
        _query(query_id="pilot-s1-q1", query="local-retrieval", language=None),
        _query(query_id="pilot-s2-q1", query="local retrieval", language=None),
    )
    _write_queries(
        exact_filter_queries,
        _query(
            query_id="pilot-s1-q1",
            query="local retrieval",
            channel="Evaluation-Channel",
            language=None,
        ),
        _query(
            query_id="pilot-s2-q1",
            query="local retrieval",
            subject_id="subject-2",
            channel="evaluation-channel",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q1",
            query="reliable",
            subject_id="subject-3",
            language=None,
        ),
        _query(
            query_id="pilot-s3-q2",
            query="preserves",
            subject_id="subject-3",
            language=None,
        ),
    )

    duplicate = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(duplicate_queries),
        "--output",
        str(duplicate_output),
        "--commit-sha",
        COMMIT_SHA,
    )
    exact_filters = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(exact_filter_queries),
        "--output",
        str(exact_filter_output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert duplicate.returncode == 2
    assert "duplicate retrieval query" in duplicate.stderr
    assert not duplicate_output.exists()
    assert exact_filters.returncode == 0, exact_filters.stderr
    assert [
        query["filters"]["channel"]
        for query in json.loads(exact_filter_output.read_text(encoding="utf-8"))["queries"]
    ][:2] == ["Evaluation-Channel", "evaluation-channel"]


def test_duplicate_detection_matches_unicode61_case_and_diacritic_behavior(
    tmp_path: Path,
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _write_queries(
        queries,
        _query(query_id="pilot-s1-q1", query="café", language=None),
        _query(query_id="pilot-s2-q1", query="CAFE", language=None),
    )

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert "duplicate retrieval query" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "legitimate_query",
    ("[TODO]", "TBD", "[hostile] OR query"),
)
def test_cli_allows_legitimate_bracketed_todo_and_tbd_queries(
    tmp_path: Path, legitimate_query: str
) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    payload = _query_payload(*_pilot_cases())
    payload["queries"][0]["query"] = legitimate_query  # type: ignore[index]
    queries.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 0, result.stderr
    assert output.exists()


def test_cli_rejects_replace_with_marker_case_insensitively(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    payload = _query_payload(*_pilot_cases())
    payload["queries"][0]["query"] = "[replace_with_query]"  # type: ignore[index]
    queries.write_text(json.dumps(payload), encoding="utf-8")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert "placeholder" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("top_k", ("0", "21"))
def test_cli_rejects_out_of_bounds_top_k(tmp_path: Path, top_k: str) -> None:
    result = _run_script(
        "--database",
        str(tmp_path / "missing.sqlite3"),
        "--queries-file",
        str(tmp_path / "missing.json"),
        "--output",
        str(tmp_path / "packet.json"),
        "--commit-sha",
        COMMIT_SHA,
        "--top-k",
        top_k,
    )

    assert result.returncode == 2
    assert "--top-k must be between 1 and 20" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_a_non_full_commit_sha_and_has_help(tmp_path: Path) -> None:
    invalid = _run_script(
        "--database",
        str(tmp_path / "database.sqlite3"),
        "--queries-file",
        str(tmp_path / "queries.json"),
        "--output",
        str(tmp_path / "packet.json"),
        "--commit-sha",
        "abc1234",
    )
    help_result = _run_executable("--help")

    assert invalid.returncode == 2
    assert "--commit-sha must be a full 40-character Git SHA" in invalid.stderr
    assert help_result.returncode == 0
    assert "--database" in help_result.stdout
    assert "--queries-file" in help_result.stdout
    assert "--force" in help_result.stdout


def test_cli_rejects_an_invalid_index(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    database.write_bytes(b"not sqlite")
    _write_queries(queries, *_pilot_cases())

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert "search index" in result.stderr
    assert "Traceback" not in result.stderr
    assert not output.exists()


def test_cli_does_not_overwrite_an_existing_target_without_force(tmp_path: Path) -> None:
    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _write_queries(queries, *_pilot_cases())
    output.write_bytes(b"existing evidence\n")

    result = _run_script(
        "--database",
        str(database),
        "--queries-file",
        str(queries),
        "--output",
        str(output),
        "--commit-sha",
        COMMIT_SHA,
    )

    assert result.returncode == 2
    assert "output already exists" in result.stderr
    assert output.read_bytes() == b"existing evidence\n"


def test_force_write_keeps_the_previous_target_if_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    output = tmp_path / "packet.json"
    output.write_bytes(b"existing evidence\n")

    def fail_replace(source: Path, target: Path) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(preparation.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected publish failure"):
        preparation._atomic_write(output, b"replacement\n", force=True)

    assert output.read_bytes() == b"existing evidence\n"
    assert list(tmp_path.iterdir()) == [output]


def test_result_without_source_provenance_is_rejected() -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    incomplete_hit = SimpleNamespace(
        document=SimpleNamespace(
            document_id="a" * 64,
            source_relpath="channel/transcripts/video.en.vtt",
            source_sha256="",
        ),
        passage=SimpleNamespace(passage_id="b" * 64),
        rank=1,
        excerpt="excerpt",
    )

    with pytest.raises(preparation.EvaluationPreparationError, match="source_sha256"):
        preparation._serialize_hit(incomplete_hit)


def test_source_replacement_after_snapshot_capture_keeps_one_valid_old_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    database = tmp_path / "search.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _build_index(replacement)
    captured_sha256 = hashlib.sha256(database.read_bytes()).hexdigest()
    _write_queries(queries, *_pilot_cases())
    actual_hash = preparation._sha256_regular_file
    hash_calls = 0

    def replace_source_after_second_hash(path: Path) -> tuple[str, int]:
        nonlocal hash_calls
        result = actual_hash(path)
        hash_calls += 1
        if hash_calls == 2:
            os.replace(replacement, database)
        return result

    monkeypatch.setattr(preparation, "_sha256_regular_file", replace_source_after_second_hash)

    exit_code = preparation.main(
        [
            "--database",
            str(database),
            "--queries-file",
            str(queries),
            "--output",
            str(output),
            "--commit-sha",
            COMMIT_SHA,
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().err == ""
    assert hash_calls == 2
    packet = json.loads(output.read_text(encoding="utf-8"))
    assert packet["index"]["sha256"] == captured_sha256
    assert packet["queries"][0]["results"][0]["text"] == (
        "reliable local retrieval preserves timestamped provenance"
    )
    assert hashlib.sha256(database.read_bytes()).hexdigest() != captured_sha256


def test_code_identity_changes_when_an_executed_file_changes(tmp_path: Path) -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    checkout = tmp_path / "checkout"
    sources: dict[str, Path] = {}
    for logical_name in CODE_FILES:
        path = checkout / logical_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"initial {logical_name}\n", encoding="utf-8")
        sources[logical_name] = path

    first = preparation._hash_code_sources(sources)
    changed_file = checkout / "src/yt_insights/search/query.py"
    changed_file.write_text("changed query implementation\n", encoding="utf-8")
    second = preparation._hash_code_sources(sources)

    assert first["sha256"] != second["sha256"]
    assert first["files"]["src/yt_insights/search/query.py"] != second["files"][
        "src/yt_insights/search/query.py"
    ]
    assert set(first["files"]) == set(second["files"]) == CODE_FILES


def test_loaded_module_sources_match_the_expected_checkout_files() -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    checkout = Path(__file__).parents[1].resolve()
    sources = preparation._loaded_code_sources(checkout)

    assert set(sources) == CODE_FILES
    assert sources["scripts/prepare_search_relevance_evaluation.py"] == SCRIPT.resolve()
    for logical_name, source in sources.items():
        assert source == (checkout / logical_name).resolve()


def test_code_is_rehashed_immediately_before_packet_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    database = tmp_path / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _write_queries(queries, *_pilot_cases())
    actual_identity = preparation._code_identity
    identity_calls = 0

    def change_identity_on_publication(checkout_root: Path) -> dict[str, object]:
        nonlocal identity_calls
        identity = actual_identity(checkout_root)
        identity_calls += 1
        if identity_calls == 3:
            return {**identity, "sha256": "f" * 64}
        return identity

    monkeypatch.setattr(preparation, "_code_identity", change_identity_on_publication)

    exit_code = preparation.main(
        [
            "--database",
            str(database),
            "--queries-file",
            str(queries),
            "--output",
            str(output),
            "--commit-sha",
            COMMIT_SHA,
        ]
    )

    assert exit_code == 2
    assert identity_calls == 3
    assert "executed code changed before packet publication" in capsys.readouterr().err
    assert not output.exists()


def test_snapshot_capture_needs_no_write_access_beside_the_index(tmp_path: Path) -> None:
    source_directory = tmp_path / "read-only-index"
    source_directory.mkdir()
    database = source_directory / "search.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _write_queries(queries, *_pilot_cases())
    database_identity_before = database.lstat()
    source_directory.chmod(0o500)
    if os.access(source_directory, os.W_OK):
        source_directory.chmod(0o700)
        pytest.skip("platform privileges bypass read-only directory permissions")

    try:
        result = _run_script(
            "--database",
            str(database),
            "--queries-file",
            str(queries),
            "--output",
            str(output),
            "--commit-sha",
            COMMIT_SHA,
        )
    finally:
        source_directory.chmod(0o700)

    assert result.returncode == 0, result.stderr
    assert output.exists()
    database_identity_after = database.lstat()
    assert (
        database_identity_after.st_dev,
        database_identity_after.st_ino,
        database_identity_after.st_nlink,
        database_identity_after.st_size,
        database_identity_after.st_mtime_ns,
        database_identity_after.st_ctime_ns,
    ) == (
        database_identity_before.st_dev,
        database_identity_before.st_ino,
        database_identity_before.st_nlink,
        database_identity_before.st_size,
        database_identity_before.st_mtime_ns,
        database_identity_before.st_ctime_ns,
    )


def test_source_replacement_during_streamed_snapshot_capture_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from scripts import prepare_search_relevance_evaluation as preparation

    database = tmp_path / "search.sqlite3"
    replacement = tmp_path / "replacement.sqlite3"
    queries = tmp_path / "queries.json"
    output = tmp_path / "packet.json"
    _build_index(database)
    _build_index(replacement)
    _write_queries(queries, *_pilot_cases())
    actual_read = preparation.os.read
    replaced = False

    def replace_source_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal replaced
        chunk = actual_read(descriptor, size)
        if not replaced:
            replaced = True
            os.replace(replacement, database)
        return chunk

    monkeypatch.setattr(preparation.os, "read", replace_source_after_first_read)

    exit_code = preparation.main(
        [
            "--database",
            str(database),
            "--queries-file",
            str(queries),
            "--output",
            str(output),
            "--commit-sha",
            COMMIT_SHA,
        ]
    )

    assert exit_code == 2
    assert "database changed while copying snapshot" in capsys.readouterr().err
    assert replaced is True
    assert not output.exists()
