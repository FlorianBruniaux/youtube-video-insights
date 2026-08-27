"""Atomic SQLite FTS5 storage for the local transcript search corpus."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any

from .corpus import CorpusManifest
from .models import BuildReport, DocumentRef, Passage, SearchHit, SearchQuery
from .query import build_fts_expression


_SCHEMA_VERSION = "1"
_INDEX_VERSION = "search-v1"
_REPORT_COUNTERS = (
    "sources_discovered",
    "sources_selected",
    "sources_invalid",
    "documents_indexed",
    "passages_indexed",
)


class SearchIndexError(RuntimeError):
    """Base error raised for local search-index failures."""


class SearchIndexNotFound(SearchIndexError):
    """Raised when no published search index is available."""


class SearchIndexInvalid(SearchIndexError):
    """Raised when a published search index cannot be trusted."""


class SQLiteFtsIndex:
    """Persist and query a validated FTS5 index at one database path."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def rebuild(self, manifest: CorpusManifest) -> BuildReport:
        """Build an index beside the active database, then atomically publish it."""
        temporary_path: Path | None = None
        connection: sqlite3.Connection | None = None
        try:
            report = self._report_from_manifest(manifest)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.database_path.parent,
                prefix=f".{self.database_path.name}.",
                suffix=".tmp",
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            connection = sqlite3.connect(temporary_path)
            connection.execute("PRAGMA foreign_keys = ON")
            self._create_schema(connection)
            with connection:
                self._insert_manifest(connection, manifest)
                self._insert_metadata(connection, report)
                self._verify_built_database(connection, report)
            connection.close()
            connection = None
            os.replace(temporary_path, self.database_path)
            temporary_path = None
            return report
        except Exception as error:
            if connection is not None:
                connection.close()
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if isinstance(error, SearchIndexError):
                raise
            raise SearchIndexError("search index rebuild failed") from error

    def status(self) -> BuildReport:
        """Return validated metadata from the active, read-only database."""
        connection = self._open_active_readonly()
        try:
            return self._validate_and_load_report(connection)
        except SearchIndexError:
            raise
        except Exception as error:
            raise SearchIndexInvalid("search index status is invalid") from error
        finally:
            connection.close()

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Search the active index using only bound parameters."""
        expression = build_fts_expression(query.text)
        connection = self._open_active_readonly()
        try:
            self._validate_and_load_report(connection)
            sql = """
                SELECT
                    documents.document_id, documents.source_relpath, documents.source_sha256,
                    documents.channel_id, documents.channel_title, documents.video_id,
                    documents.video_title, documents.language,
                    passages.passage_id, passages.ordinal, passages.start_seconds,
                    passages.end_seconds, passages.text, passages.youtube_url,
                    bm25(passages_fts) AS bm25_score
                FROM passages_fts
                JOIN passages ON passages.passage_id = passages_fts.passage_id
                JOIN documents ON documents.document_id = passages.document_id
                WHERE passages_fts MATCH ?
            """
            parameters: list[Any] = [expression]
            if query.channel is not None:
                sql += " AND documents.channel_id = ?"
                parameters.append(query.channel)
            if query.language is not None:
                sql += " AND documents.language = ?"
                parameters.append(query.language)
            sql += " ORDER BY bm25_score ASC, passages.passage_id ASC LIMIT ?"
            parameters.append(query.limit)
            rows = connection.execute(sql, parameters).fetchall()
            return tuple(self._row_to_hit(row, rank) for rank, row in enumerate(rows, start=1))
        except SearchIndexError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError) as error:
            raise SearchIndexInvalid("search index query is invalid") from error
        finally:
            connection.close()

    def _open_active_readonly(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise SearchIndexNotFound("search index does not exist")
        try:
            connection = sqlite3.connect(f"{self.database_path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error as error:
            raise SearchIndexInvalid("search index cannot be opened") from error
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY NOT NULL,
                source_relpath TEXT NOT NULL UNIQUE,
                source_sha256 TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_title TEXT NOT NULL,
                video_id TEXT NOT NULL,
                video_title TEXT NOT NULL,
                language TEXT NOT NULL
            );
            CREATE TABLE passages (
                passage_id TEXT PRIMARY KEY NOT NULL,
                document_id TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                text TEXT NOT NULL,
                youtube_url TEXT NOT NULL,
                UNIQUE (document_id, ordinal)
            );
            CREATE VIRTUAL TABLE passages_fts USING fts5(
                passage_id UNINDEXED,
                video_title,
                text,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            CREATE TABLE index_meta (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _insert_manifest(connection: sqlite3.Connection, manifest: CorpusManifest) -> None:
        connection.executemany(
            """
            INSERT INTO documents (
                document_id, source_relpath, source_sha256, channel_id, channel_title,
                video_id, video_title, language
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document.document_id,
                    document.source_relpath,
                    document.source_sha256,
                    document.channel_id,
                    document.channel_title,
                    document.video_id,
                    document.video_title,
                    document.language,
                )
                for document in manifest.documents
            ],
        )
        connection.executemany(
            """
            INSERT INTO passages (
                passage_id, document_id, ordinal, start_seconds, end_seconds, text, youtube_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    passage.passage_id,
                    passage.document_id,
                    passage.ordinal,
                    passage.start_seconds,
                    passage.end_seconds,
                    passage.text,
                    passage.youtube_url,
                )
                for passage in manifest.passages
            ],
        )
        documents = {document.document_id: document for document in manifest.documents}
        connection.executemany(
            "INSERT INTO passages_fts (passage_id, video_title, text) VALUES (?, ?, ?)",
            [
                (passage.passage_id, documents[passage.document_id].video_title, passage.text)
                for passage in manifest.passages
            ],
        )

    @staticmethod
    def _insert_metadata(connection: sqlite3.Connection, report: BuildReport) -> None:
        metadata = {
            "schema_version": _SCHEMA_VERSION,
            "index_version": _INDEX_VERSION,
            **{name: str(getattr(report, name)) for name in _REPORT_COUNTERS},
            "invalid_sources": json.dumps(
                list(report.invalid_sources), ensure_ascii=False, separators=(",", ":")
            ),
        }
        connection.executemany(
            "INSERT INTO index_meta (key, value) VALUES (?, ?)", metadata.items()
        )

    @staticmethod
    def _verify_built_database(connection: sqlite3.Connection, report: BuildReport) -> None:
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SearchIndexInvalid("rebuilt search index has foreign-key errors")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise SearchIndexInvalid("rebuilt search index failed integrity check")
        counts = {
            "documents_indexed": connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "passages_indexed": connection.execute("SELECT count(*) FROM passages").fetchone()[0],
            "fts_passages": connection.execute("SELECT count(*) FROM passages_fts").fetchone()[0],
        }
        if (
            counts["documents_indexed"] != report.documents_indexed
            or counts["passages_indexed"] != report.passages_indexed
            or counts["fts_passages"] != report.passages_indexed
        ):
            raise SearchIndexInvalid("rebuilt search index has unexpected row counts")
        fts_mismatch = connection.execute(
            """
            SELECT EXISTS(
                SELECT 1
                FROM passages
                LEFT JOIN passages_fts ON passages_fts.passage_id = passages.passage_id
                JOIN documents ON documents.document_id = passages.document_id
                WHERE passages_fts.passage_id IS NULL
                   OR passages_fts.text IS NOT passages.text
                   OR passages_fts.video_title IS NOT documents.video_title
                UNION ALL
                SELECT 1
                FROM passages_fts
                LEFT JOIN passages ON passages.passage_id = passages_fts.passage_id
                WHERE passages.passage_id IS NULL
            )
            """
        ).fetchone()[0]
        if fts_mismatch:
            raise SearchIndexInvalid("rebuilt search index FTS rows do not match passages")

    @staticmethod
    def _report_from_manifest(manifest: CorpusManifest) -> BuildReport:
        invalid_sources = tuple(sorted(item.source_relpath for item in manifest.invalid_sources))
        return BuildReport(
            sources_discovered=manifest.sources_discovered,
            sources_selected=manifest.sources_selected - manifest.sources_invalid,
            sources_invalid=manifest.sources_invalid,
            documents_indexed=len(manifest.documents),
            passages_indexed=len(manifest.passages),
            invalid_sources=invalid_sources,
        )

    def _validate_and_load_report(self, connection: sqlite3.Connection) -> BuildReport:
        try:
            self._validate_schema(connection)
            metadata = dict(connection.execute("SELECT key, value FROM index_meta"))
            if (
                metadata.get("schema_version") != _SCHEMA_VERSION
                or metadata.get("index_version") != _INDEX_VERSION
            ):
                raise SearchIndexInvalid("search index version is unsupported")
            report = BuildReport(
                **{name: int(metadata[name]) for name in _REPORT_COUNTERS},
                invalid_sources=self._load_invalid_sources(metadata["invalid_sources"]),
            )
            self._validate_report_invalid_sources(report)
            self._validate_persisted_domain_records(connection)
            self._verify_built_database(connection, report)
            return report
        except SearchIndexError:
            raise
        except (KeyError, TypeError, ValueError, sqlite3.Error) as error:
            raise SearchIndexInvalid("search index metadata is invalid") from error

    @staticmethod
    def _validate_report_invalid_sources(report: BuildReport) -> None:
        if (
            report.sources_invalid != len(report.invalid_sources)
            or report.invalid_sources != tuple(sorted(report.invalid_sources))
            or len(set(report.invalid_sources)) != len(report.invalid_sources)
        ):
            raise ValueError("invalid source metadata does not match report")

    @staticmethod
    def _validate_persisted_domain_records(connection: sqlite3.Connection) -> None:
        documents: dict[str, DocumentRef] = {}
        for row in connection.execute(
            """
            SELECT document_id, source_relpath, source_sha256, channel_id, channel_title,
                   video_id, video_title, language
            FROM documents
            """
        ):
            document = SQLiteFtsIndex._row_to_document(row)
            documents[document.document_id] = document
        for row in connection.execute(
            """
            SELECT passage_id, document_id, ordinal, start_seconds, end_seconds, text, youtube_url
            FROM passages
            """
        ):
            passage = SQLiteFtsIndex._row_to_passage(row)
            document = documents.get(passage.document_id)
            if document is None:
                raise ValueError("passage document is missing")
            SearchHit(document=document, passage=passage, rank=1, score=0.0)

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected_business_columns = {
            "documents": (
                ("document_id", "TEXT", 1, 1),
                ("source_relpath", "TEXT", 1, 0),
                ("source_sha256", "TEXT", 1, 0),
                ("channel_id", "TEXT", 1, 0),
                ("channel_title", "TEXT", 1, 0),
                ("video_id", "TEXT", 1, 0),
                ("video_title", "TEXT", 1, 0),
                ("language", "TEXT", 1, 0),
            ),
            "passages": (
                ("passage_id", "TEXT", 1, 1),
                ("document_id", "TEXT", 1, 0),
                ("ordinal", "INTEGER", 1, 0),
                ("start_seconds", "REAL", 1, 0),
                ("end_seconds", "REAL", 1, 0),
                ("text", "TEXT", 1, 0),
                ("youtube_url", "TEXT", 1, 0),
            ),
            "index_meta": (
                ("key", "TEXT", 1, 1),
                ("value", "TEXT", 1, 0),
            ),
        }
        try:
            for table, columns in expected_business_columns.items():
                table_info = list(connection.execute(f"PRAGMA table_info({table})"))
                actual = tuple((row[1], row[2].upper(), row[3], row[5]) for row in table_info)
                if actual != columns:
                    raise SearchIndexInvalid("search index schema is invalid")
            fts_columns = tuple(
                row[1] for row in connection.execute("PRAGMA table_info(passages_fts)")
            )
            if fts_columns != ("passage_id", "video_title", "text"):
                raise SearchIndexInvalid("search index FTS schema is invalid")
            if not SQLiteFtsIndex._has_unique_index(connection, "documents", ("source_relpath",)):
                raise SearchIndexInvalid("search index schema is invalid")
            if not SQLiteFtsIndex._has_unique_index(
                connection, "passages", ("document_id", "ordinal")
            ):
                raise SearchIndexInvalid("search index schema is invalid")
            foreign_keys = list(connection.execute("PRAGMA foreign_key_list(passages)"))
            if not any(
                row[2] == "documents"
                and row[3] == "document_id"
                and row[4] == "document_id"
                and row[6] == "CASCADE"
                for row in foreign_keys
            ):
                raise SearchIndexInvalid("search index schema is invalid")
            fts_sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'passages_fts'"
            ).fetchone()
            if fts_sql is None or not SQLiteFtsIndex._has_expected_fts5_configuration(fts_sql[0]):
                raise SearchIndexInvalid("search index FTS schema is invalid")
        except sqlite3.Error as error:
            raise SearchIndexInvalid("search index schema is invalid") from error

    @staticmethod
    def _has_unique_index(
        connection: sqlite3.Connection, table: str, columns: tuple[str, ...]
    ) -> bool:
        for index in connection.execute(f"PRAGMA index_list({table})"):
            if index[2] and tuple(
                row[2] for row in connection.execute(f"PRAGMA index_info({index[1]})")
            ) == columns:
                return True
        return False

    @staticmethod
    def _has_expected_fts5_configuration(ddl: str) -> bool:
        match = re.search(r"\bUSING\s+fts5\s*\(", ddl, flags=re.IGNORECASE)
        if match is None:
            return False
        arguments = SQLiteFtsIndex._split_fts5_arguments(ddl[match.end() :])
        if arguments is None or len(arguments) != 4:
            return False
        passage_id, video_title, text, tokenizer = arguments
        tokenizer_match = re.fullmatch(
            r"tokenize\s*=\s*'((?:''|[^'])*)'\s*", tokenizer, flags=re.IGNORECASE | re.DOTALL
        )
        return (
            re.fullmatch(r"passage_id\s+UNINDEXED\s*", passage_id, flags=re.IGNORECASE)
            is not None
            and re.fullmatch(r"video_title\s*", video_title, flags=re.IGNORECASE) is not None
            and re.fullmatch(r"text\s*", text, flags=re.IGNORECASE) is not None
            and tokenizer_match is not None
            and tokenizer_match.group(1).replace("''", "'").lower()
            == "unicode61 remove_diacritics 2"
        )

    @staticmethod
    def _split_fts5_arguments(ddl_tail: str) -> tuple[str, ...] | None:
        arguments: list[str] = []
        current: list[str] = []
        nested_parentheses = 0
        quote: str | None = None
        position = 0
        while position < len(ddl_tail):
            character = ddl_tail[position]
            if quote is not None:
                current.append(character)
                if character == quote:
                    if position + 1 < len(ddl_tail) and ddl_tail[position + 1] == quote:
                        current.append(ddl_tail[position + 1])
                        position += 1
                    else:
                        quote = None
            elif character in "'\"`":
                quote = character
                current.append(character)
            elif character == "(":
                nested_parentheses += 1
                current.append(character)
            elif character == ")":
                if nested_parentheses == 0:
                    arguments.append("".join(current).strip())
                    remainder = ddl_tail[position + 1 :].strip()
                    if remainder not in ("", ";") or any(not argument for argument in arguments):
                        return None
                    return tuple(arguments)
                nested_parentheses -= 1
                current.append(character)
            elif character == "," and nested_parentheses == 0:
                arguments.append("".join(current).strip())
                current = []
            else:
                current.append(character)
            position += 1
        return None

    @staticmethod
    def _load_invalid_sources(value: str) -> tuple[str, ...]:
        decoded = json.loads(value)
        if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
            raise ValueError("invalid source metadata")
        return tuple(decoded)

    @staticmethod
    def _row_to_hit(row: sqlite3.Row, rank: int) -> SearchHit:
        document = SQLiteFtsIndex._row_to_document(row)
        passage = SQLiteFtsIndex._row_to_passage(row)
        return SearchHit(document=document, passage=passage, rank=rank, score=-float(row["bm25_score"]))

    @staticmethod
    def _row_to_document(row: sqlite3.Row) -> DocumentRef:
        return DocumentRef(
            document_id=row["document_id"],
            source_relpath=row["source_relpath"],
            source_sha256=row["source_sha256"],
            channel_id=row["channel_id"],
            channel_title=row["channel_title"],
            video_id=row["video_id"],
            video_title=row["video_title"],
            language=row["language"],
        )

    @staticmethod
    def _row_to_passage(row: sqlite3.Row) -> Passage:
        return Passage(
            passage_id=row["passage_id"],
            document_id=row["document_id"],
            ordinal=row["ordinal"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            text=row["text"],
            youtube_url=row["youtube_url"],
        )
