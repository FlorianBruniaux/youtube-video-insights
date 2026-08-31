#!/usr/bin/env python3
"""Prepare a deterministic, unreviewed relevance-evaluation packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite
from pathlib import Path, PurePosixPath

import yt_insights as yt_insights_module
import yt_insights.search as search_package_module
import yt_insights.search.chunker as search_chunker_module
import yt_insights.search.corpus as search_corpus_module
import yt_insights.search.models as search_models_module
import yt_insights.search.query as search_query_module
import yt_insights.search.service as search_service_module
import yt_insights.search.sqlite_fts as search_sqlite_fts_module
import yt_insights.vtt_parser as vtt_parser_module
from yt_insights.search.models import SearchHit, SearchQuery, youtube_url
from yt_insights.search.query import build_fts_expression
from yt_insights.search.service import SearchService
from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex

SCHEMA_VERSION = 1
MAX_QUERIES_FILE_BYTES = 1_048_576
MAX_QUERIES = 500
MAX_IDENTIFIER_CODEPOINTS = 100
MAX_LABEL_CODEPOINTS = 300
MAX_QUERY_CODEPOINTS = 500
MAX_FILTER_CODEPOINTS = 300
MAX_CODE_FILE_BYTES = 2_097_152
_COMMIT_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_GENERATION_ID_RE = re.compile(r"[0-9a-f]{32}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_BCP47_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_PLACEHOLDER_RE = re.compile(r"replace_with", re.IGNORECASE)
_TOP_LEVEL_KEYS = {
    "schema_version",
    "status",
    "instructions",
    "pilot",
    "release",
    "queries",
}
_QUERY_KEYS = {
    "id",
    "phase",
    "subject_id",
    "label",
    "query",
    "category",
    "query_language",
    "filters",
}
_FILTER_KEYS = {"channel", "language"}
_PILOT_KEYS = {
    "required_distinct_subjects",
    "required_query_count",
    "results_per_query",
    "required_result_judgment_count",
    "minimum_relevant_result_count_for_pilot_pass",
    "relevant_grades",
    "note",
}
_RELEASE_KEYS = {
    "minimum_query_case_count",
    "maximum_query_case_count",
    "required_categories",
    "required_metrics",
}
_REQUIRED_CATEGORIES = (
    "exact",
    "natural_question",
    "paraphrase",
    "bilingual",
    "filter",
    "hostile",
    "no_answer",
)
_REQUIRED_METRICS = ("Recall@5", "MRR@10", "nDCG@10", "zero-result rate")
_PILOT_CONSTANTS = {
    "required_distinct_subjects": 3,
    "required_query_count": 4,
    "results_per_query": 5,
    "required_result_judgment_count": 20,
    "minimum_relevant_result_count_for_pilot_pass": 16,
    "relevant_grades": [1, 2],
}
_CODE_FILES = (
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
)
_LOADED_MODULES = {
    "src/yt_insights/__init__.py": yt_insights_module,
    "src/yt_insights/vtt_parser.py": vtt_parser_module,
    "src/yt_insights/search/__init__.py": search_package_module,
    "src/yt_insights/search/chunker.py": search_chunker_module,
    "src/yt_insights/search/corpus.py": search_corpus_module,
    "src/yt_insights/search/models.py": search_models_module,
    "src/yt_insights/search/query.py": search_query_module,
    "src/yt_insights/search/service.py": search_service_module,
    "src/yt_insights/search/sqlite_fts.py": search_sqlite_fts_module,
}


class EvaluationPreparationError(ValueError):
    """Raised when an evaluation packet cannot be prepared safely."""


@dataclass(frozen=True, slots=True)
class QueryCase:
    """One ordered retrieval request and its non-judgment metadata."""

    identifier: str
    phase: str
    subject_id: str
    label: str
    query: str
    category: str
    query_language: str
    channel: str | None
    language: str | None

    def search_query(self, top_k: int) -> SearchQuery:
        return SearchQuery(
            text=self.query,
            channel=self.channel,
            language=self.language,
            limit=top_k,
        )

    def output_metadata(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            "phase": self.phase,
            "subject_id": self.subject_id,
            "label": self.label,
            "query": self.query,
            "category": self.category,
            "query_language": self.query_language,
            "filters": {"channel": self.channel, "language": self.language},
        }


@dataclass(frozen=True, slots=True)
class QuerySet:
    """Validated query-file metadata plus its ordered retrieval cases."""

    status: str
    instructions: tuple[str, ...]
    pilot: dict[str, object]
    release: dict[str, object]
    queries: tuple[QueryCase, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class PinnedIndexSnapshot:
    """One private database generation captured for the complete preparation."""

    database: Path


def _bounded_integer(option: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"{option} must be between {minimum} and {maximum}"
            ) from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{option} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def _commit_sha(value: str) -> str:
    if _COMMIT_SHA_RE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("--commit-sha must be a full 40-character Git SHA")
    return value.lower()


def _nonblank_string(name: str, value: object, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EvaluationPreparationError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if "\0" in normalized:
        raise EvaluationPreparationError(f"{name} must not contain a NUL byte")
    if len(normalized) > maximum:
        raise EvaluationPreparationError(
            f"{name} must not exceed {maximum} Unicode code points"
        )
    return normalized


def _optional_filter(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _nonblank_string(name, value, MAX_FILTER_CODEPOINTS)


def _fts_duplicate_key(query_text: str) -> str:
    expression = build_fts_expression(query_text)
    decomposed = unicodedata.normalize("NFKD", expression).casefold()
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _same_json_value(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return len(actual) == len(expected) and all(
            _same_json_value(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def _validate_pilot(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _PILOT_KEYS:
        raise EvaluationPreparationError(
            "pilot must contain exactly required_distinct_subjects, required_query_count, "
            "results_per_query, required_result_judgment_count, "
            "minimum_relevant_result_count_for_pilot_pass, relevant_grades, and note"
        )
    if any(
        not _same_json_value(value[key], expected)
        for key, expected in _PILOT_CONSTANTS.items()
    ):
        raise EvaluationPreparationError(
            "pilot metadata constants must match the tracked template"
        )
    _nonblank_string("pilot note", value["note"], 1_000)
    return dict(value)


def _validate_release(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != _RELEASE_KEYS:
        raise EvaluationPreparationError(
            "release must contain exactly minimum_query_case_count, "
            "maximum_query_case_count, required_categories, and required_metrics"
        )
    if (
        value["minimum_query_case_count"] != 60
        or value["maximum_query_case_count"] != 100
        or value["required_categories"] != list(_REQUIRED_CATEGORIES)
        or value["required_metrics"] != list(_REQUIRED_METRICS)
    ):
        raise EvaluationPreparationError(
            "release metadata constants must match the tracked template"
        )
    return dict(value)


def _regular_file_bytes(path: Path, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationPreparationError(f"file does not exist: {path}") from error
    except OSError as error:
        raise EvaluationPreparationError(f"file cannot be inspected: {path}") from error
    if not stat.S_ISREG(before.st_mode):
        raise EvaluationPreparationError(f"file must be a regular file: {path}")
    if before.st_size > maximum:
        raise EvaluationPreparationError(f"file exceeds {maximum} bytes: {path}")
    try:
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if _complete_file_identity(opened) != _complete_file_identity(before):
                raise EvaluationPreparationError(f"file changed while opening: {path}")
            payload = source.read(maximum + 1)
        after = path.lstat()
    except EvaluationPreparationError:
        raise
    except OSError as error:
        raise EvaluationPreparationError(f"file cannot be read: {path}") from error
    if len(payload) > maximum:
        raise EvaluationPreparationError(f"file exceeds {maximum} bytes: {path}")
    if _complete_file_identity(after) != _complete_file_identity(before):
        raise EvaluationPreparationError(f"file changed while reading: {path}")
    return payload


def _link_file_identity(details: os.stat_result) -> tuple[int, int, int, int]:
    return (details.st_dev, details.st_ino, details.st_size, details.st_mtime_ns)


def _complete_file_identity(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (*_link_file_identity(details), details.st_ctime_ns)


def _read_generation_id(database: Path) -> str:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database.absolute().as_uri()}?mode=ro", uri=True
        )
        connection.execute("PRAGMA query_only = ON")
        row = connection.execute(
            "SELECT value FROM index_meta WHERE key = ?", ("generation_id",)
        ).fetchone()
    except sqlite3.Error as error:
        raise EvaluationPreparationError(
            "search index generation metadata is invalid"
        ) from error
    finally:
        if connection is not None:
            connection.close()
    generation_id = None if row is None else row[0]
    if (
        not isinstance(generation_id, str)
        or _GENERATION_ID_RE.fullmatch(generation_id) is None
    ):
        raise EvaluationPreparationError("search index generation metadata is invalid")
    return generation_id


def _write_new_private_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags, 0o600)
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
    except OSError as error:
        raise EvaluationPreparationError("snapshot receipt cannot be copied safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _copy_database_snapshot(source: Path, destination: Path) -> tuple[int, int, int, int, int]:
    try:
        path_details = source.lstat()
    except FileNotFoundError as error:
        raise EvaluationPreparationError(f"database does not exist: {source}") from error
    except OSError as error:
        raise EvaluationPreparationError("database cannot be inspected") from error
    if not stat.S_ISREG(path_details.st_mode):
        raise EvaluationPreparationError("database must be a regular file")
    expected_identity = _complete_file_identity(path_details)
    source_descriptor: int | None = None
    destination_descriptor: int | None = None
    try:
        source_descriptor = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        )
        if _complete_file_identity(os.fstat(source_descriptor)) != expected_identity:
            raise EvaluationPreparationError("database changed while opening snapshot source")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        while chunk := os.read(source_descriptor, 1_048_576):
            written = 0
            while written < len(chunk):
                written += os.write(destination_descriptor, chunk[written:])
        os.fsync(destination_descriptor)
        descriptor_identity = _complete_file_identity(os.fstat(source_descriptor))
        path_identity = _complete_file_identity(source.lstat())
        if descriptor_identity != expected_identity or path_identity != expected_identity:
            raise EvaluationPreparationError("database changed while copying snapshot")
        return expected_identity
    except EvaluationPreparationError:
        raise
    except OSError as error:
        raise EvaluationPreparationError("database snapshot cannot be copied") from error
    finally:
        if destination_descriptor is not None:
            os.close(destination_descriptor)
        if source_descriptor is not None:
            os.close(source_descriptor)


@contextmanager
def _pinned_index_snapshot(database: Path) -> Iterator[PinnedIndexSnapshot]:
    index_directory = database.absolute().parent.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if temporary_root == index_directory or index_directory in temporary_root.parents:
        raise EvaluationPreparationError(
            "database snapshot directory must be outside the index directory"
        )
    with tempfile.TemporaryDirectory(
        dir=temporary_root, prefix="yt-insights-evaluation-"
    ) as directory:
        snapshot_database = Path(directory) / database.name
        _copy_database_snapshot(database, snapshot_database)

        generation_id = _read_generation_id(snapshot_database)
        source_receipt = database.absolute().with_name(
            f".{database.name}.{generation_id}.receipt.json"
        )
        snapshot_receipt = snapshot_database.with_name(
            f".{snapshot_database.name}.{generation_id}.receipt.json"
        )
        receipt_payload = _regular_file_bytes(source_receipt, 4_096)
        _write_new_private_file(snapshot_receipt, receipt_payload)
        yield PinnedIndexSnapshot(database=snapshot_database)


def _reject_nonstandard_json_constant(value: str) -> object:
    raise EvaluationPreparationError(f"queries JSON contains non-standard value {value}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    decoded: dict[str, object] = {}
    for key, value in pairs:
        if key in decoded:
            raise EvaluationPreparationError(f"duplicate JSON object key: {key}")
        decoded[key] = value
    return decoded


def _reject_placeholders(value: object, location: str = "$") -> None:
    if isinstance(value, str):
        if _PLACEHOLDER_RE.search(value) is not None:
            raise EvaluationPreparationError(
                f"queries JSON contains an unresolved placeholder at {location}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_placeholders(item, f"{location}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_placeholders(key, f"{location}.<key>")
            _reject_placeholders(item, f"{location}.{key}")


def load_query_set(path: Path) -> QuerySet:
    """Load and strictly validate the explicit ordered query JSON file."""
    raw = _regular_file_bytes(path, MAX_QUERIES_FILE_BYTES)
    try:
        decoded = json.loads(
            raw.decode("utf-8"),
            parse_constant=_reject_nonstandard_json_constant,
            object_pairs_hook=_reject_duplicate_object_keys,
        )
    except UnicodeDecodeError as error:
        raise EvaluationPreparationError("queries file must be valid UTF-8") from error
    except json.JSONDecodeError as error:
        raise EvaluationPreparationError("queries file must contain valid JSON") from error
    if not isinstance(decoded, dict) or set(decoded) != _TOP_LEVEL_KEYS:
        raise EvaluationPreparationError(
            "queries JSON must contain exactly schema_version, status, instructions, "
            "pilot, release, and queries"
        )
    _reject_placeholders(decoded)
    if type(decoded["schema_version"]) is not int or decoded["schema_version"] != 1:
        raise EvaluationPreparationError("queries schema_version must equal 1")
    status_value = _nonblank_string("status", decoded["status"], MAX_LABEL_CODEPOINTS)
    if status_value != "UNKNOWN":
        raise EvaluationPreparationError("status must equal UNKNOWN")
    instructions_value = decoded["instructions"]
    if not isinstance(instructions_value, list) or not instructions_value:
        raise EvaluationPreparationError("instructions must be a non-empty JSON array")
    instructions = tuple(
        _nonblank_string("instruction", instruction, 1_000)
        for instruction in instructions_value
    )
    pilot = _validate_pilot(decoded["pilot"])
    release = _validate_release(decoded["release"])
    queries_value = decoded["queries"]
    if not isinstance(queries_value, list) or not queries_value:
        raise EvaluationPreparationError("queries must be a non-empty JSON array")
    if len(queries_value) > MAX_QUERIES:
        raise EvaluationPreparationError(f"queries must not contain more than {MAX_QUERIES} items")

    queries: list[QueryCase] = []
    identifiers: set[str] = set()
    retrieval_contracts: set[tuple[str, str | None, str | None]] = set()
    for position, value in enumerate(queries_value, start=1):
        if not isinstance(value, dict) or set(value) != _QUERY_KEYS:
            raise EvaluationPreparationError(
                f"query {position} must contain exactly id, phase, subject_id, label, "
                "query, category, query_language, and filters"
            )
        filters = value["filters"]
        if not isinstance(filters, dict) or set(filters) != _FILTER_KEYS:
            raise EvaluationPreparationError(
                f"query {position} filters must contain exactly channel and language"
            )
        identifier = _nonblank_string(
            f"query {position} id", value["id"], MAX_IDENTIFIER_CODEPOINTS
        )
        if identifier in identifiers:
            raise EvaluationPreparationError(f"duplicate query id: {identifier}")
        identifiers.add(identifier)
        query_text = _nonblank_string(
            f"query {position} query", value["query"], MAX_QUERY_CODEPOINTS
        )
        channel = _optional_filter(f"query {position} channel", filters["channel"])
        language = _optional_filter(f"query {position} language", filters["language"])
        retrieval_contract = (
            _fts_duplicate_key(query_text),
            channel,
            language,
        )
        if retrieval_contract in retrieval_contracts:
            raise EvaluationPreparationError(f"duplicate retrieval query: {identifier}")
        retrieval_contracts.add(retrieval_contract)
        queries.append(
            QueryCase(
                identifier=identifier,
                phase=_nonblank_string(
                    f"query {position} phase", value["phase"], MAX_IDENTIFIER_CODEPOINTS
                ),
                subject_id=_nonblank_string(
                    f"query {position} subject_id",
                    value["subject_id"],
                    MAX_IDENTIFIER_CODEPOINTS,
                ),
                label=_nonblank_string(
                    f"query {position} label", value["label"], MAX_LABEL_CODEPOINTS
                ),
                query=query_text,
                category=_nonblank_string(
                    f"query {position} category",
                    value["category"],
                    MAX_IDENTIFIER_CODEPOINTS,
                ),
                query_language=_nonblank_string(
                    f"query {position} query_language",
                    value["query_language"],
                    MAX_IDENTIFIER_CODEPOINTS,
                ),
                channel=channel,
                language=language,
            )
        )
        if queries[-1].phase not in {"pilot", "release"}:
            raise EvaluationPreparationError(
                f"query {position} phase must be pilot or release"
            )
        if queries[-1].category not in _REQUIRED_CATEGORIES:
            raise EvaluationPreparationError(f"query {position} category is unsupported")
        if _BCP47_RE.fullmatch(queries[-1].query_language) is None:
            raise EvaluationPreparationError(
                f"query {position} query_language must be a conservative BCP47 tag"
            )
    pilot_queries = tuple(query for query in queries if query.phase == "pilot")
    release_queries = tuple(query for query in queries if query.phase == "release")
    if len(pilot_queries) != 4:
        raise EvaluationPreparationError("queries must contain exactly 4 pilot queries")
    if len({query.subject_id for query in pilot_queries}) < 3:
        raise EvaluationPreparationError(
            "pilot queries must cover at least 3 distinct pilot subjects"
        )
    if release_queries and not 60 <= len(release_queries) <= 100:
        raise EvaluationPreparationError(
            "release query count must be between 60 and 100 when release queries are present"
        )
    if release_queries and {
        query.category for query in release_queries
    } != set(_REQUIRED_CATEGORIES):
        raise EvaluationPreparationError(
            "release queries must cover every required category"
        )
    return QuerySet(
        status=status_value,
        instructions=instructions,
        pilot=pilot,
        release=release,
        queries=tuple(queries),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _sha256_regular_file(path: Path) -> tuple[str, int]:
    try:
        before = path.lstat()
    except FileNotFoundError as error:
        raise EvaluationPreparationError(f"database does not exist: {path}") from error
    except OSError as error:
        raise EvaluationPreparationError(f"database cannot be inspected: {path}") from error
    if not stat.S_ISREG(before.st_mode):
        raise EvaluationPreparationError("database must be a regular file")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            if _complete_file_identity(opened) != _complete_file_identity(before):
                raise EvaluationPreparationError("database changed while opening")
            while chunk := source.read(1_048_576):
                digest.update(chunk)
        after = path.lstat()
    except EvaluationPreparationError:
        raise
    except OSError as error:
        raise EvaluationPreparationError("database cannot be hashed") from error
    if _complete_file_identity(after) != _complete_file_identity(before):
        raise EvaluationPreparationError("database changed while hashing")
    return digest.hexdigest(), before.st_size


def _loaded_code_sources(checkout_root: Path) -> dict[str, Path]:
    root = checkout_root.resolve(strict=True)
    sources = {
        "scripts/prepare_search_relevance_evaluation.py": Path(__file__).resolve(
            strict=True
        )
    }
    for logical_name, module in _LOADED_MODULES.items():
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str) or not module_file:
            raise EvaluationPreparationError(
                f"loaded module has no source path: {logical_name}"
            )
        sources[logical_name] = Path(module_file).resolve(strict=True)
    for logical_name, source in sources.items():
        expected = (root / logical_name).resolve(strict=True)
        if source != expected:
            raise EvaluationPreparationError(
                f"loaded module source is outside the expected checkout: {logical_name}"
            )
    return sources


def _hash_code_sources(sources: dict[str, Path]) -> dict[str, object]:
    if set(sources) != set(_CODE_FILES):
        raise EvaluationPreparationError("loaded code source inventory is incomplete")
    files = {
        logical_name: hashlib.sha256(
            _regular_file_bytes(sources[logical_name], MAX_CODE_FILE_BYTES)
        ).hexdigest()
        for logical_name in _CODE_FILES
    }
    encoded = json.dumps(
        files, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {"sha256": hashlib.sha256(encoded).hexdigest(), "files": files}


def _code_identity(checkout_root: Path) -> dict[str, object]:
    return _hash_code_sources(_loaded_code_sources(checkout_root))


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise EvaluationPreparationError(f"search result lacks valid {name}")
    return value


def _serialize_hit(hit: SearchHit) -> dict[str, object]:
    try:
        document = hit.document
        passage = hit.passage
        document_id = _require_sha256("document_id", document.document_id)
        passage_id = _require_sha256("passage_id", passage.passage_id)
        source_sha256 = _require_sha256("source_sha256", document.source_sha256)
        source_relpath = _nonblank_string("source_relpath", document.source_relpath, 4_096)
        source_path = PurePosixPath(source_relpath)
        if "\\" in source_relpath or source_path.is_absolute() or ".." in source_path.parts:
            raise EvaluationPreparationError("search result lacks valid source_relpath")
        video_id = _nonblank_string("video_id", document.video_id, 100)
        text = _nonblank_string("passage text", passage.text, 1_000_000)
        excerpt = _nonblank_string("passage excerpt", hit.excerpt, 1_000_000)
        if passage.document_id != document_id:
            raise EvaluationPreparationError("search result passage lacks document provenance")
        if not isinstance(hit.rank, int) or isinstance(hit.rank, bool) or hit.rank < 1:
            raise EvaluationPreparationError("search result rank must be a positive integer")
        if (
            isinstance(passage.start_seconds, bool)
            or not isinstance(passage.start_seconds, (int, float))
            or not isfinite(passage.start_seconds)
            or passage.start_seconds < 0
            or isinstance(passage.end_seconds, bool)
            or not isinstance(passage.end_seconds, (int, float))
            or not isfinite(passage.end_seconds)
            or passage.end_seconds < passage.start_seconds
        ):
            raise EvaluationPreparationError("search result lacks valid timestamps")
        canonical_url = youtube_url(video_id, passage.start_seconds)
        if passage.youtube_url != canonical_url:
            raise EvaluationPreparationError("search result lacks canonical YouTube provenance")
        if not isinstance(passage.ordinal, int) or isinstance(passage.ordinal, bool):
            raise EvaluationPreparationError("search result lacks valid passage ordinal")
        return {
            "rank": hit.rank,
            "document_id": document_id,
            "passage_id": passage_id,
            "source_relpath": source_relpath,
            "source_sha256": source_sha256,
            "channel_id": _nonblank_string("channel_id", document.channel_id, 300),
            "channel_title": _nonblank_string(
                "channel_title", document.channel_title, 1_000
            ),
            "video_id": video_id,
            "video_title": _nonblank_string("video_title", document.video_title, 10_000),
            "language": _nonblank_string("language", document.language, 100),
            "ordinal": passage.ordinal,
            "start_seconds": passage.start_seconds,
            "end_seconds": passage.end_seconds,
            "text": text,
            "excerpt": excerpt,
            "youtube_url": canonical_url,
            "judgment": {
                "relevance": None,
                "reviewer": None,
                "reviewed_at": None,
                "notes": None,
            },
        }
    except EvaluationPreparationError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise EvaluationPreparationError(
            "search result lacks required provenance"
        ) from error


def prepare_packet(
    *, database: Path, query_set: QuerySet, commit_sha: str, top_k: int
) -> dict[str, object]:
    """Search a validated index and return an unreviewed deterministic packet."""
    if top_k < 5:
        raise EvaluationPreparationError("--top-k must be at least 5 for pilot queries")
    if any(query.phase == "release" for query in query_set.queries) and top_k != 10:
        raise EvaluationPreparationError(
            "--top-k must equal 10 when release queries are present"
        )
    checkout_root = Path(__file__).resolve().parents[1]
    code_identity = _code_identity(checkout_root)
    index = SQLiteFtsIndex(database)
    report = index.status()
    index_sha256, index_size = _sha256_regular_file(database)
    service = SearchService(index)
    rendered_queries: list[dict[str, object]] = []
    for query_case in query_set.queries:
        hits = service.search(query_case.search_query(top_k))
        rendered_query = query_case.output_metadata()
        rendered_query["results"] = [_serialize_hit(hit) for hit in hits]
        rendered_queries.append(rendered_query)
    final_sha256, final_size = _sha256_regular_file(database)
    if (final_sha256, final_size) != (index_sha256, index_size):
        raise EvaluationPreparationError("database changed while preparing evaluation")
    if _code_identity(checkout_root) != code_identity:
        raise EvaluationPreparationError("executed code changed while preparing evaluation")
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": "prepare-search-relevance-evaluation-v1",
        "commit_sha": commit_sha,
        "code": code_identity,
        "index": {
            "sha256": index_sha256,
            "size_bytes": index_size,
            "sources_discovered": report.sources_discovered,
            "sources_selected": report.sources_selected,
            "sources_invalid": report.sources_invalid,
            "documents_indexed": report.documents_indexed,
            "passages_indexed": report.passages_indexed,
        },
        "query_set": {
            "sha256": query_set.sha256,
            "schema_version": 1,
            "status": query_set.status,
            "instructions": list(query_set.instructions),
            "pilot": query_set.pilot,
            "release": query_set.release,
        },
        "top_k": top_k,
        "queries": rendered_queries,
        "evaluation": {
            "status": "UNKNOWN",
            "threshold": None,
            "method": None,
            "reviewer": None,
            "reviewed_at": None,
            "decision": None,
            "notes": None,
        },
    }


def _target_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as error:
        raise EvaluationPreparationError("output path cannot be inspected") from error
    return True


def _reject_input_output_alias(output: Path, inputs: Sequence[Path]) -> None:
    output_resolved = output.resolve(strict=False)
    for input_path in inputs:
        try:
            input_resolved = input_path.resolve(strict=True)
        except OSError as error:
            raise EvaluationPreparationError(f"input path cannot be resolved: {input_path}") from error
        if output_resolved == input_resolved:
            raise EvaluationPreparationError("output must not replace an input file")


def _atomic_write(
    path: Path,
    payload: bytes,
    *,
    force: bool,
) -> None:
    if not force and _target_exists(path):
        raise EvaluationPreparationError("output already exists; pass --force to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    published = False
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        if force:
            os.replace(temporary, path)
        else:
            try:
                os.link(temporary, path)
            except FileExistsError as error:
                raise EvaluationPreparationError(
                    "output already exists; pass --force to replace it"
                ) from error
            temporary.unlink()
        published = True
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a deterministic relevance-evaluation JSON packet without "
            "making human judgments."
        )
    )
    parser.add_argument("--database", required=True, type=Path, help="existing FTS5 index")
    parser.add_argument(
        "--queries-file", required=True, type=Path, help="explicit ordered query JSON"
    )
    parser.add_argument("--output", required=True, type=Path, help="packet JSON path")
    parser.add_argument(
        "--commit-sha",
        required=True,
        type=_commit_sha,
        help="full 40-character Git commit SHA to record",
    )
    parser.add_argument(
        "--top-k",
        type=_bounded_integer("--top-k", 1, 20),
        default=5,
        help="maximum results per query, from 1 to 20 (default: 5)",
    )
    parser.add_argument(
        "--force", action="store_true", help="atomically replace an existing output"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        _reject_input_output_alias(
            arguments.output, (arguments.database, arguments.queries_file)
        )
        if not arguments.force and _target_exists(arguments.output):
            raise EvaluationPreparationError(
                "output already exists; pass --force to replace it"
            )
        query_set = load_query_set(arguments.queries_file)
        with _pinned_index_snapshot(arguments.database) as snapshot:
            packet = prepare_packet(
                database=snapshot.database,
                query_set=query_set,
                commit_sha=arguments.commit_sha,
                top_k=arguments.top_k,
            )
            encoded = (
                json.dumps(
                    packet,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            checkout_root = Path(__file__).resolve().parents[1]
            if _code_identity(checkout_root) != packet["code"]:
                raise EvaluationPreparationError(
                    "executed code changed before packet publication"
                )
            _atomic_write(
                arguments.output,
                encoded,
                force=arguments.force,
            )
    except (EvaluationPreparationError, OSError, SearchIndexError, ValueError) as error:
        print(f"evaluation packet error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
