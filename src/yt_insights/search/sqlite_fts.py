"""Atomic SQLite FTS5 storage for the local transcript search corpus."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from .corpus import CorpusManifest
from .models import BuildReport, DocumentRef, Passage, SearchHit, SearchQuery
from .query import build_fts_expression

_SCHEMA_VERSION = "1"
_INDEX_VERSION = "search-v1"
_GENERATION_ID_RE = re.compile(r"[0-9a-f]{32}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EXCERPT_CONTEXT_CODEPOINTS = 80
_MAX_EXCERPT_WINDOWS = 8
_MAX_RECEIPT_BYTES = 4096
_REPORT_COUNTERS = (
    "sources_discovered",
    "sources_selected",
    "sources_invalid",
    "documents_indexed",
    "passages_indexed",
)
_FileIdentity = tuple[int, int, int, int, int]


class SearchIndexError(RuntimeError):
    """Base error raised for local search-index failures."""


class SearchIndexNotFound(SearchIndexError):
    """Raised when no published search index is available."""


class SearchIndexInvalid(SearchIndexError):
    """Raised when a published search index cannot be trusted."""


class SearchPassageNotFound(SearchIndexError):
    """Raised when a requested passage is absent from a trusted index."""


class SQLiteFtsIndex:
    """Persist and query a validated FTS5 index at one database path."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self._validated_identity: _FileIdentity | None = None
        self._validated_sha256: str | None = None

    def rebuild(self, manifest: CorpusManifest) -> BuildReport:
        """Build an index beside the active database, then atomically publish it."""
        temporary_path: Path | None = None
        connection: sqlite3.Connection | None = None
        try:
            report = self._report_from_manifest(manifest)
            generation_id = secrets.token_hex(16)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.database_path.parent,
                prefix=f".{self.database_path.name}.",
                suffix=".tmp",
            )
            os.close(descriptor)
            temporary_path = Path(temporary_name)
            connection = sqlite3.connect(temporary_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            self._create_schema(connection)
            with connection:
                self._insert_manifest(connection, manifest)
                self._insert_metadata(connection, report, generation_id)
                validated_report = self._validate_and_load_report(connection)
                if validated_report != report:
                    raise SearchIndexInvalid("rebuilt search index report does not match manifest")
            connection.close()
            connection = None
            self._write_generation_receipt(generation_id, temporary_path)
            os.replace(temporary_path, self.database_path)
            temporary_path = None
            self._validated_identity = None
            self._validated_sha256 = None
            return validated_report
        except Exception as error:
            if connection is not None:
                connection.close()
            if temporary_path is not None:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
            if isinstance(error, SearchIndexError):
                raise
            raise SearchIndexError("search index rebuild failed") from error

    def status(self) -> BuildReport:
        """Return validated metadata from the active, read-only database."""
        connection, identity = self._open_active_readonly()
        try:
            report = self._validate_and_load_report(connection)
            self._validate_generation_receipt(connection, identity)
            self._require_database_identity(identity)
            return report
        except SearchIndexError:
            raise
        except Exception as error:
            raise SearchIndexInvalid("search index status is invalid") from error
        finally:
            connection.close()

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Search the active index using only bound parameters."""
        expression = f"text : ({build_fts_expression(query.text)})"
        highlight_start, highlight_end = self._highlight_markers()
        connection, identity = self._open_active_readonly()
        try:
            self._validate_query_contract(connection, identity)
            sql = """
                SELECT
                    documents.document_id, documents.source_relpath, documents.source_sha256,
                    documents.channel_id, documents.channel_title, documents.video_id,
                    documents.video_title, documents.language,
                    passages.passage_id, passages.ordinal, passages.start_seconds,
                    passages.end_seconds, passages.text, passages.youtube_url,
                    highlight(passages_fts, 2, ?, ?) AS highlighted_text,
                    bm25(passages_fts, 0.0, 0.0, 1.0) AS bm25_score
                FROM passages_fts
                JOIN passages
                  ON passages.rowid = passages_fts.rowid
                 AND passages.passage_id = passages_fts.passage_id
                JOIN documents ON documents.document_id = passages.document_id
                WHERE passages_fts MATCH ?
            """
            parameters: list[Any] = [highlight_start, highlight_end, expression]
            if query.channel is not None:
                sql += " AND documents.channel_id = ?"
                parameters.append(query.channel)
            if query.language is not None:
                sql += " AND documents.language = ?"
                parameters.append(query.language)
            sql += " ORDER BY bm25_score ASC, passages.passage_id ASC LIMIT ?"
            parameters.append(query.limit)
            rows = connection.execute(sql, parameters).fetchall()
            hits = tuple(
                self._row_to_hit(row, rank, highlight_start, highlight_end)
                for rank, row in enumerate(rows, start=1)
            )
            self._require_database_identity(identity)
            return hits
        except SearchIndexError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError) as error:
            raise SearchIndexInvalid("search index query is invalid") from error
        finally:
            connection.close()

    def get_passage(self, passage_id: str) -> tuple[DocumentRef, Passage]:
        """Load one passage and its document without issuing an FTS query."""
        connection, identity = self._open_active_readonly()
        try:
            self._validate_query_contract(connection, identity)
            row = connection.execute(
                """
                SELECT
                    documents.document_id, documents.source_relpath,
                    documents.source_sha256, documents.channel_id,
                    documents.channel_title, documents.video_id,
                    documents.video_title, documents.language,
                    passages.passage_id, passages.ordinal,
                    passages.start_seconds, passages.end_seconds,
                    passages.text, passages.youtube_url
                FROM passages
                JOIN documents ON documents.document_id = passages.document_id
                WHERE passages.passage_id = ?
                """,
                (passage_id,),
            ).fetchone()
            if row is None:
                self._require_database_identity(identity)
                raise SearchPassageNotFound("passage does not exist")
            document = self._row_to_document(row)
            passage = self._row_to_passage(row)
            SearchHit(document=document, passage=passage, rank=1, score=0.0)
            self._require_database_identity(identity)
            return document, passage
        except SearchIndexError:
            raise
        except (sqlite3.Error, TypeError, ValueError, KeyError) as error:
            raise SearchIndexInvalid("search index passage is invalid") from error
        finally:
            connection.close()

    def _open_active_readonly(self) -> tuple[sqlite3.Connection, _FileIdentity]:
        identity = self._database_identity()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self.database_path.absolute().as_uri()}?mode=ro", uri=True
            )
            connection.execute("PRAGMA query_only = ON")
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise SearchIndexInvalid("search index cannot be opened") from error
        connection.row_factory = sqlite3.Row
        return connection, identity

    def _database_identity(self) -> _FileIdentity:
        try:
            details = self.database_path.lstat()
        except FileNotFoundError as error:
            raise SearchIndexNotFound("search index does not exist") from error
        except OSError as error:
            raise SearchIndexInvalid("search index path cannot be inspected") from error
        if not stat.S_ISREG(details.st_mode):
            raise SearchIndexInvalid("search index path must be a regular file")
        return self._identity_from_stat(details)

    def _require_database_identity(self, expected: _FileIdentity) -> None:
        try:
            current = self._database_identity()
        except SearchIndexError as error:
            raise SearchIndexInvalid("search index changed during access") from error
        if current != expected:
            raise SearchIndexInvalid("search index changed during access")

    def _validate_query_contract(
        self, connection: sqlite3.Connection, identity: _FileIdentity
    ) -> None:
        try:
            metadata = dict(
                connection.execute(
                    """
                    SELECT key, value
                    FROM index_meta
                    WHERE key IN (?, ?, ?)
                    """,
                    ("schema_version", "index_version", "generation_id"),
                )
            )
        except sqlite3.Error as error:
            raise SearchIndexInvalid("search index query contract is invalid") from error
        if (
            metadata.get("schema_version") != _SCHEMA_VERSION
            or metadata.get("index_version") != _INDEX_VERSION
        ):
            raise SearchIndexInvalid("search index version is unsupported")
        generation_id = self._require_generation_id(metadata.get("generation_id"))
        self._load_and_validate_receipt(generation_id, identity)

    def _validate_generation_receipt(
        self, connection: sqlite3.Connection, identity: _FileIdentity
    ) -> None:
        try:
            row = connection.execute(
                "SELECT value FROM index_meta WHERE key = ?", ("generation_id",)
            ).fetchone()
        except sqlite3.Error as error:
            raise SearchIndexInvalid("search index generation metadata is invalid") from error
        generation_id = self._require_generation_id(None if row is None else row[0])
        self._load_and_validate_receipt(generation_id, identity)

    def _write_generation_receipt(
        self, generation_id: str, validated_database: Path
    ) -> None:
        generation_id = self._require_generation_id(generation_id)
        try:
            details = validated_database.lstat()
        except OSError as error:
            raise SearchIndexInvalid("validated search index cannot be inspected") from error
        if not stat.S_ISREG(details.st_mode):
            raise SearchIndexInvalid("validated search index must be a regular file")
        identity = self._identity_from_stat(details)
        database_sha256 = self._sha256_regular_file(validated_database, identity)
        payload = {
            "generation_id": generation_id,
            "database_sha256": database_sha256,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        receipt_path = self._receipt_path(generation_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(receipt_path, flags, 0o600)
            written = 0
            while written < len(encoded):
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
        except OSError as error:
            raise SearchIndexInvalid("search index validation receipt cannot be written") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        try:
            final_identity = self._identity_from_stat(validated_database.lstat())
        except OSError as error:
            raise SearchIndexInvalid(
                "validated search index cannot be inspected"
            ) from error
        if final_identity != identity:
            raise SearchIndexInvalid("validated search index changed before publication")

    def _load_and_validate_receipt(
        self, generation_id: str, expected_identity: _FileIdentity
    ) -> None:
        receipt_path = self._receipt_path(generation_id)
        try:
            path_details = receipt_path.lstat()
        except OSError as error:
            raise SearchIndexInvalid("search index validation receipt is missing") from error
        if not stat.S_ISREG(path_details.st_mode):
            raise SearchIndexInvalid("search index validation receipt must be a regular file")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(receipt_path, flags)
            descriptor_details = os.fstat(descriptor)
            if (
                not stat.S_ISREG(descriptor_details.st_mode)
                or (descriptor_details.st_dev, descriptor_details.st_ino)
                != (path_details.st_dev, path_details.st_ino)
                or descriptor_details.st_size > _MAX_RECEIPT_BYTES
            ):
                raise SearchIndexInvalid("search index validation receipt is invalid")
            encoded = os.read(descriptor, _MAX_RECEIPT_BYTES + 1)
            if len(encoded) > _MAX_RECEIPT_BYTES:
                raise SearchIndexInvalid("search index validation receipt is invalid")
        except SearchIndexError:
            raise
        except OSError as error:
            raise SearchIndexInvalid("search index validation receipt cannot be read") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

        try:
            payload = json.loads(encoded.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SearchIndexInvalid("search index validation receipt is malformed") from error
        if not isinstance(payload, dict) or set(payload) != {
            "generation_id",
            "database_sha256",
        }:
            raise SearchIndexInvalid("search index validation receipt is malformed")
        database_sha256 = payload.get("database_sha256")
        if not isinstance(database_sha256, str) or _SHA256_RE.fullmatch(database_sha256) is None:
            raise SearchIndexInvalid("search index validation receipt is malformed")
        if payload.get("generation_id") != generation_id:
            raise SearchIndexInvalid("search index validation receipt does not match database")
        if (
            self._validated_identity != expected_identity
            or self._validated_sha256 != database_sha256
        ):
            actual_sha256 = self._sha256_regular_file(
                self.database_path, expected_identity
            )
            self._require_database_identity(expected_identity)
            if actual_sha256 != database_sha256:
                raise SearchIndexInvalid(
                    "search index validation receipt does not match database"
                )
            self._validated_identity = expected_identity
            self._validated_sha256 = database_sha256

    def _receipt_path(self, generation_id: str) -> Path:
        generation_id = self._require_generation_id(generation_id)
        database_path = self.database_path.absolute()
        receipt_name = f".{database_path.name}.{generation_id}.receipt.json"
        receipt_path = database_path.with_name(receipt_name)
        if receipt_path.parent != database_path.parent or receipt_path.name != receipt_name:
            raise SearchIndexInvalid("search index validation receipt path is invalid")
        return receipt_path

    @staticmethod
    def _require_generation_id(value: object) -> str:
        if not isinstance(value, str) or _GENERATION_ID_RE.fullmatch(value) is None:
            raise SearchIndexInvalid("search index generation id is invalid")
        return value

    @staticmethod
    def _identity_from_stat(details: os.stat_result) -> _FileIdentity:
        return (
            details.st_dev,
            details.st_ino,
            details.st_size,
            details.st_mtime_ns,
            details.st_ctime_ns,
        )

    @staticmethod
    def _sha256_regular_file(path: Path, expected_identity: _FileIdentity) -> str:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(path, flags)
            opened_identity = SQLiteFtsIndex._identity_from_stat(os.fstat(descriptor))
            if opened_identity != expected_identity:
                raise SearchIndexInvalid("search index changed during validation")
            digest = hashlib.sha256()
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
            final_identity = SQLiteFtsIndex._identity_from_stat(os.fstat(descriptor))
            if final_identity != expected_identity:
                raise SearchIndexInvalid("search index changed during validation")
            return digest.hexdigest()
        except SearchIndexError:
            raise
        except OSError as error:
            raise SearchIndexInvalid("search index cannot be hashed") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

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
            (
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
            ),
        )
        connection.executemany(
            """
            INSERT INTO passages (
                rowid, passage_id, document_id, ordinal, start_seconds, end_seconds, text,
                youtube_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    rowid,
                    passage.passage_id,
                    passage.document_id,
                    passage.ordinal,
                    passage.start_seconds,
                    passage.end_seconds,
                    passage.text,
                    passage.youtube_url,
                )
                for rowid, passage in enumerate(manifest.passages, start=1)
            ),
        )
        documents = {document.document_id: document for document in manifest.documents}
        connection.executemany(
            """
            INSERT INTO passages_fts (rowid, passage_id, video_title, text)
            VALUES (?, ?, ?, ?)
            """,
            (
                (
                    rowid,
                    passage.passage_id,
                    documents[passage.document_id].video_title,
                    passage.text,
                )
                for rowid, passage in enumerate(manifest.passages, start=1)
            ),
        )

    @staticmethod
    def _insert_metadata(
        connection: sqlite3.Connection, report: BuildReport, generation_id: str
    ) -> None:
        metadata = {
            "schema_version": _SCHEMA_VERSION,
            "index_version": _INDEX_VERSION,
            "generation_id": generation_id,
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
                LEFT JOIN passages_fts ON passages_fts.rowid = passages.rowid
                JOIN documents ON documents.document_id = passages.document_id
                WHERE passages_fts.rowid IS NULL
                   OR passages_fts.passage_id IS NOT passages.passage_id
                   OR passages_fts.text IS NOT passages.text
                   OR passages_fts.video_title IS NOT documents.video_title
                UNION ALL
                SELECT 1
                FROM passages_fts
                LEFT JOIN passages ON passages.rowid = passages_fts.rowid
                WHERE passages.rowid IS NULL
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
            self._require_generation_id(metadata.get("generation_id"))
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
            matched_document = documents.get(passage.document_id)
            if matched_document is None:
                raise ValueError("passage document is missing")
            SearchHit(
                document=matched_document, passage=passage, rank=1, score=0.0
            )

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
    def _row_to_hit(
        row: sqlite3.Row, rank: int, highlight_start: str, highlight_end: str
    ) -> SearchHit:
        document = SQLiteFtsIndex._row_to_document(row)
        passage = SQLiteFtsIndex._row_to_passage(row)
        return SearchHit(
            document=document,
            passage=passage,
            rank=rank,
            score=-float(row["bm25_score"]),
            excerpt=SQLiteFtsIndex._build_excerpt(
                passage.text, row["highlighted_text"], highlight_start, highlight_end
            ),
        )

    @staticmethod
    def _build_excerpt(
        text: str, highlighted_text: str, highlight_start: str, highlight_end: str
    ) -> str:
        match_spans = SQLiteFtsIndex._highlight_spans(
            highlighted_text, text, highlight_start, highlight_end
        )
        windows = sorted(
            (
                max(0, start - _EXCERPT_CONTEXT_CODEPOINTS),
                min(len(text), end + _EXCERPT_CONTEXT_CODEPOINTS),
            )
            for start, end in match_spans
        )
        merged_windows: list[tuple[int, int]] = []
        for start, end in windows:
            if merged_windows and start <= merged_windows[-1][1]:
                merged_windows[-1] = (merged_windows[-1][0], max(end, merged_windows[-1][1]))
            else:
                merged_windows.append((start, end))

        if len(merged_windows) > _MAX_EXCERPT_WINDOWS:
            last_index = len(merged_windows) - 1
            selected_indexes = tuple(
                index * last_index // (_MAX_EXCERPT_WINDOWS - 1)
                for index in range(_MAX_EXCERPT_WINDOWS)
            )
            selected_windows = [merged_windows[index] for index in selected_indexes]
        else:
            selected_windows = merged_windows

        omitted_matches = sum(
            not any(
                window_start <= match_start and match_end <= window_end
                for window_start, window_end in selected_windows
            )
            for match_start, match_end in match_spans
        )
        fragments = [text[start:end].strip() for start, end in selected_windows]
        excerpt = " … ".join(fragment for fragment in fragments if fragment)
        if selected_windows[0][0] > 0:
            excerpt = f"… {excerpt}"
        if selected_windows[-1][1] < len(text):
            excerpt = f"{excerpt} …"
        if omitted_matches:
            excerpt = f"{excerpt} [{omitted_matches} matched terms outside bounded excerpt]"
        return excerpt

    @staticmethod
    def _highlight_spans(
        highlighted_text: str, text: str, highlight_start: str, highlight_end: str
    ) -> tuple[tuple[int, int], ...]:
        plain_parts: list[str] = []
        spans: list[tuple[int, int]] = []
        cursor = 0
        plain_length = 0
        while cursor < len(highlighted_text):
            start = highlighted_text.find(highlight_start, cursor)
            stray_end = highlighted_text.find(highlight_end, cursor)
            if stray_end != -1 and (start == -1 or stray_end < start):
                raise ValueError("invalid FTS highlight markers")
            if start == -1:
                tail = highlighted_text[cursor:]
                plain_parts.append(tail)
                plain_length += len(tail)
                cursor = len(highlighted_text)
                break
            prefix = highlighted_text[cursor:start]
            plain_parts.append(prefix)
            plain_length += len(prefix)
            marked_start = start + len(highlight_start)
            end = highlighted_text.find(highlight_end, marked_start)
            nested_start = highlighted_text.find(highlight_start, marked_start)
            if end == -1 or (nested_start != -1 and nested_start < end):
                raise ValueError("invalid FTS highlight markers")
            marked_text = highlighted_text[marked_start:end]
            if not marked_text:
                raise ValueError("empty FTS highlight span")
            match_start = plain_length
            plain_parts.append(marked_text)
            plain_length += len(marked_text)
            spans.append((match_start, plain_length))
            cursor = end + len(highlight_end)
        if not spans or "".join(plain_parts) != text:
            raise ValueError("FTS highlight text does not match passage")
        return tuple(spans)

    @staticmethod
    def _highlight_markers() -> tuple[str, str]:
        marker_id = secrets.token_hex(24)
        return (f"[[YT-START-{marker_id}]]", f"[[YT-END-{marker_id}]]")

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
