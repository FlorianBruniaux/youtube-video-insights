"""Bounded, path-free projections for the local web application."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Final, Protocol

from yt_insights.catalog import Catalog, CatalogError, ReadOnlyCatalog
from yt_insights.search.models import BuildReport
from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex

_MAX_PAGE_SIZE: Final = 100
_MAX_METADATA_VALUES: Final = 20
_MAX_MANIFEST_BYTES: Final = 64 * 1024
_MAX_DOSSIER_BYTES: Final = 1024 * 1024
# Export roots are user-managed directories. This observable inventory limit
# bounds all direct-child examinations across both hierarchy levels.
_EXPORT_INVENTORY_LIMIT: Final = 32
_DIRECTORY_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
_MANIFEST_FIELDS: Final = frozenset(
    {
        "acquisition_outcomes",
        "assessment",
        "candidates",
        "coverage_limits",
        "decisions",
        "dossier_sha256",
        "evidence",
        "format_version",
        "package_version",
        "session",
    }
)
_SESSION_ID: Final = re.compile(r"[A-Za-z0-9_-]{1,128}")
_VIDEO_ID: Final = re.compile(r"[A-Za-z0-9_-]{11}")
_LANGUAGE: Final = re.compile(r"[A-Za-z0-9-]{1,32}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


class SearchIndexProjection(Protocol):
    """Bounded search-index facts required by the web catalog projection."""

    def status(self) -> BuildReport: ...

    def indexed_video_ids(self, video_ids: tuple[str, ...]) -> frozenset[str]: ...


class SearchIndexWebReader:
    """Expose validated index counts and exact membership without document paths."""

    def __init__(self, index: SQLiteFtsIndex) -> None:
        if not isinstance(index, SQLiteFtsIndex):
            raise TypeError("index must be a SQLiteFtsIndex")
        self._index = index

    def status(self) -> BuildReport:
        return self._index.status()

    def indexed_video_ids(self, video_ids: tuple[str, ...]) -> frozenset[str]:
        if not isinstance(video_ids, tuple) or len(video_ids) > _MAX_PAGE_SIZE:
            raise ValueError("video IDs must be a bounded tuple")
        if any(_VIDEO_ID.fullmatch(video_id) is None for video_id in video_ids):
            raise ValueError("video ID is invalid")
        if not video_ids:
            return frozenset()
        connection: sqlite3.Connection | None = None
        try:
            # Validate the full immutable index before this narrow membership read.
            self._index.status()
            connection, identity = self._index._open_active_readonly()
            self._index._validate_query_contract(connection, identity)
            placeholders = ",".join("?" for _ in video_ids)
            rows = connection.execute(
                f"SELECT DISTINCT video_id FROM documents "
                f"WHERE video_id IN ({placeholders}) ORDER BY video_id",
                video_ids,
            ).fetchall()
            indexed = frozenset(str(row[0]) for row in rows)
            if not indexed.issubset(video_ids) or any(
                _VIDEO_ID.fullmatch(video_id) is None for video_id in indexed
            ):
                raise ValueError("search index row is invalid")
            self._index._require_database_identity(identity)
            return indexed
        except SearchIndexError:
            raise
        except (sqlite3.Error, TypeError, ValueError, OSError) as exc:
            raise SearchIndexError("search index membership query failed") from exc
        finally:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()


def _require_page(limit: int, offset: int) -> None:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= _MAX_PAGE_SIZE
    ):
        raise ValueError(f"limit must be between 1 and {_MAX_PAGE_SIZE}")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")


class CatalogWebReader:
    """Read a bounded public catalog page from one immutable database snapshot."""

    def __init__(
        self,
        catalog_database: Path,
        *,
        search_index: SearchIndexProjection | None = None,
    ) -> None:
        self._catalog_database = Path(catalog_database)
        if not self._catalog_database.is_absolute():
            raise ValueError("catalog database path must be absolute")
        self._search_index = search_index

    def corpus_status(self) -> dict[str, object]:
        """Return validated corpus counts without exposing database locations."""
        with Catalog.open_read_only(self._catalog_database) as catalog:
            catalog._require_database_identity()
            connection = catalog._connection_or_raise()
            try:
                row = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM videos) AS videos, "
                    "(SELECT COUNT(*) FROM artifacts WHERE kind = 'transcript') "
                    "AS transcripts"
                ).fetchone()
                videos = catalog._validate_nonnegative_count(row["videos"])
                transcripts = catalog._validate_nonnegative_count(row["transcripts"])
                catalog._require_database_identity()
            except CatalogError:
                raise
            except (sqlite3.Error, TypeError, ValueError, OSError) as exc:
                raise CatalogError("catalog status query failed") from exc
        index = self._search_index
        if index is None:
            return {
                "health": "partial",
                "videos": videos,
                "transcripts": transcripts,
                "documents_indexed": None,
                "passages_indexed": None,
            }
        report = index.status()
        return {
            "health": "ready",
            "videos": videos,
            "transcripts": transcripts,
            "documents_indexed": report.documents_indexed,
            "passages_indexed": report.passages_indexed,
        }

    def list_sources(self, *, limit: int, offset: int) -> dict[str, object]:
        """Return path-free metadata for a stable, paginated source inventory."""
        _require_page(limit, offset)
        with Catalog.open_read_only(self._catalog_database) as catalog:
            catalog._require_database_identity()
            connection = catalog._connection_or_raise()
            try:
                rows = connection.execute(
                    """
                    SELECT
                        videos.video_id,
                        videos.title,
                        videos.published_at,
                        COUNT(DISTINCT artifacts.id) AS artifact_count
                    FROM videos
                    LEFT JOIN artifacts ON artifacts.video_id = videos.video_id
                    GROUP BY videos.video_id
                    ORDER BY videos.video_id ASC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
                video_ids = tuple(
                    catalog._validate_video_identity(
                        row["video_id"],
                        f"https://www.youtube.com/watch?v={row['video_id']}",
                    )[0]
                    for row in rows
                )
                indexed_ids = (
                    frozenset()
                    if self._search_index is None
                    else self._search_index.indexed_video_ids(video_ids)
                )
                items = [
                    self._source_item(
                        catalog,
                        connection,
                        row,
                        indexed=(str(row["video_id"]) in indexed_ids),
                        index_known=self._search_index is not None,
                    )
                    for row in rows
                ]
                catalog._require_database_identity()
            except CatalogError:
                raise
            except (TypeError, ValueError, OSError) as exc:
                raise CatalogError("catalog query failed") from exc
        return {"items": items, "limit": limit, "offset": offset}

    @staticmethod
    def _source_item(
        catalog: ReadOnlyCatalog,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        indexed: bool,
        index_known: bool,
    ) -> dict[str, object]:
        # ReadOnlyCatalog has already validated the immutable schema. Its row
        # validators keep this additional projection bounded even if a database
        # was modified after it was initially written by YT Insights.
        video_id, url = catalog._validate_video_identity(
            row["video_id"],
            f"https://www.youtube.com/watch?v={row['video_id']}",
        )
        title = catalog._validate_metadata_text(row["title"], maximum=1000)
        published_at = catalog._validate_published_at(row["published_at"])
        artifact_count = catalog._validate_nonnegative_count(row["artifact_count"])
        transcript_row = connection.execute(
            "SELECT COUNT(*) AS transcript_count FROM artifacts "
            "WHERE video_id = ? AND kind = 'transcript'",
            (video_id,),
        ).fetchone()
        transcript_count = catalog._validate_nonnegative_count(
            transcript_row["transcript_count"]
        )
        language_rows = connection.execute(
            """
            SELECT DISTINCT language FROM artifacts
            WHERE video_id = ?
            ORDER BY language ASC
            LIMIT ?
            """,
            (video_id, _MAX_METADATA_VALUES),
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT source_slug FROM video_sources
            WHERE video_id = ?
            ORDER BY source_slug ASC
            LIMIT ?
            """,
            (video_id, _MAX_METADATA_VALUES),
        ).fetchall()
        languages = []
        for language_row in language_rows:
            language = language_row["language"]
            if not isinstance(language, str) or _LANGUAGE.fullmatch(language) is None:
                raise CatalogError("catalog row is invalid")
            languages.append(language)
        sources = [
            catalog._validate_source_slug(source_row["source_slug"])
            for source_row in source_rows
        ]
        return {
            "video_id": video_id,
            "title": title,
            "published_at": published_at,
            "languages": languages,
            "sources": sources,
            "url": url,
            "artifact_count": artifact_count,
            "transcript_state": "available" if transcript_count else "missing",
            "index_state": (
                "indexed" if indexed else "not_indexed" if index_known else "unknown"
            ),
        }


class _ExportInventory:
    """Track one bounded, explicitly partial export-directory inventory."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.examined = 0
        self.complete = True

    def names(self, directory_fd: int) -> list[str]:
        """Return sorted names examined before the fixed global budget ends."""
        names: list[str] = []
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if self.examined >= self.limit:
                        self.complete = False
                        break
                    self.examined += 1
                    names.append(entry.name)
        except OSError:
            self.complete = False
        return sorted(names)


class ExportReader:
    """Read safe summaries of locally generated research dossiers."""

    def __init__(self, exports: Path) -> None:
        self._exports = Path(exports)
        if not self._exports.is_absolute():
            raise ValueError("exports path must be absolute")

    def list_exports(self, *, limit: int) -> dict[str, object]:
        """Return safe dossiers with explicit bounded-inventory metadata.

        ``inventory_examined`` counts root and topic child entries inspected
        against ``inventory_limit``. A false ``inventory_complete`` means valid
        dossiers may remain unexamined, and therefore sets ``truncated``.
        """
        _require_page(limit, 0)
        inventory = _ExportInventory(_EXPORT_INVENTORY_LIMIT)
        try:
            root_details = self._exports.lstat()
        except FileNotFoundError:
            return self._response([], limit=limit, inventory=inventory)
        if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
            return self._response([], limit=limit, inventory=inventory)
        try:
            root_fd = os.open(self._exports, _DIRECTORY_FLAGS)
        except OSError:
            inventory.complete = False
            return self._response([], limit=limit, inventory=inventory)
        try:
            opened_root = os.fstat(root_fd)
            if (opened_root.st_dev, opened_root.st_ino) != (
                root_details.st_dev,
                root_details.st_ino,
            ):
                inventory.complete = False
                return self._response([], limit=limit, inventory=inventory)
            items: list[dict[str, object]] = []
            for topic_name in inventory.names(root_fd):
                topic_fd, topic_unavailable = self._open_directory(root_fd, topic_name)
                if topic_unavailable:
                    inventory.complete = False
                if topic_fd is None:
                    continue
                try:
                    for dossier_name in inventory.names(topic_fd):
                        item, dossier_unavailable = self._export_item(
                            topic_name, topic_fd, dossier_name
                        )
                        if dossier_unavailable:
                            inventory.complete = False
                        if item is not None:
                            items.append(item)
                finally:
                    os.close(topic_fd)
            return self._response(items, limit=limit, inventory=inventory)
        finally:
            os.close(root_fd)

    @staticmethod
    def _response(
        items: list[dict[str, object]], *, limit: int, inventory: _ExportInventory
    ) -> dict[str, object]:
        ordered_items = sorted(
            items,
            key=lambda item: (
                str(item["name"]),
                str(item["session_id"] or ""),
                str(item["created_at"] or ""),
                str(item["manifest_valid"]),
            ),
        )
        truncated = not inventory.complete or len(ordered_items) > limit
        return {
            "items": ordered_items[:limit],
            "limit": limit,
            "truncated": truncated,
            "inventory_complete": inventory.complete,
            "inventory_examined": inventory.examined,
            "inventory_limit": inventory.limit,
        }

    @staticmethod
    def _open_directory(parent_fd: int, name: str) -> tuple[int | None, bool]:
        """Open a candidate directory or report whether its safety is unknown."""
        if Path(name).name != name or name in {"", ".", ".."} or "\x00" in name:
            return (None, False)
        try:
            details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            return (None, True)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            return (None, False)
        try:
            directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError:
            return (None, True)
        try:
            opened = os.fstat(directory_fd)
        except OSError:
            with suppress(OSError):
                os.close(directory_fd)
            return (None, True)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            with suppress(OSError):
                os.close(directory_fd)
            return (None, True)
        return (directory_fd, False)

    @staticmethod
    def _export_item(
        topic_name: str, parent_fd: int, name: str
    ) -> tuple[dict[str, object] | None, bool]:
        directory_fd, directory_unavailable = ExportReader._open_directory(
            parent_fd, name
        )
        if directory_fd is None:
            return (None, directory_unavailable)
        try:
            session_id, created_at, _dossier_sha256, manifest_unavailable = (
                ExportReader._manifest_summary(directory_fd)
            )
        finally:
            os.close(directory_fd)
        manifest_valid = session_id is not None and created_at is not None
        export_id = ExportReader._export_id(topic_name, name)
        return (
            {
                "name": name,
                "session_id": session_id if manifest_valid else None,
                "created_at": created_at if manifest_valid else None,
                "manifest_valid": manifest_valid,
                "export_id": export_id,
                "open_url": (
                    f"/api/v1/exports/{export_id}/dossier"
                    if manifest_valid
                    else None
                ),
            },
            manifest_unavailable,
        )

    @staticmethod
    def _manifest_summary(
        directory_fd: int,
    ) -> tuple[str | None, str | None, str | None, bool]:
        try:
            details = os.stat("manifest.json", dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return (None, None, None, False)
        except OSError:
            return (None, None, None, True)
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_size > _MAX_MANIFEST_BYTES
        ):
            return (None, None, None, False)
        try:
            manifest_fd = os.open("manifest.json", _READ_FLAGS, dir_fd=directory_fd)
        except OSError:
            return (None, None, None, True)
        try:
            try:
                opened = os.fstat(manifest_fd)
            except OSError:
                return (None, None, None, True)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
            ):
                return (None, None, None, True)
            try:
                contents = os.read(manifest_fd, _MAX_MANIFEST_BYTES + 1)
                final = os.fstat(manifest_fd)
            except OSError:
                return (None, None, None, True)
        finally:
            os.close(manifest_fd)
        if (
            len(contents) > _MAX_MANIFEST_BYTES
            or len(contents) != opened.st_size
            or ExportReader._file_identity(opened)
            != ExportReader._file_identity(final)
        ):
            return (None, None, None, True)
        try:
            manifest = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (None, None, None, False)
        session_id, created_at, dossier_sha256 = ExportReader._validate_manifest(
            manifest
        )
        return (session_id, created_at, dossier_sha256, False)

    @staticmethod
    def _validate_manifest(
        manifest: object,
    ) -> tuple[str | None, str | None, str | None]:
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
            return (None, None, None)
        if (
            manifest.get("format_version") != 1
            or not isinstance(manifest.get("package_version"), str)
            or not _SHA256.fullmatch(str(manifest.get("dossier_sha256", "")))
            or not all(
                isinstance(manifest.get(name), list)
                for name in (
                    "acquisition_outcomes",
                    "candidates",
                    "coverage_limits",
                    "decisions",
                    "evidence",
                )
            )
            or (
                manifest.get("assessment") is not None
                and not isinstance(manifest.get("assessment"), dict)
            )
        ):
            return (None, None, None)
        session = manifest.get("session")
        if not isinstance(session, dict):
            return (None, None, None)
        session_id = session.get("session_id")
        created_at = session.get("created_at")
        if (
            not isinstance(session_id, str)
            or _SESSION_ID.fullmatch(session_id) is None
            or not isinstance(created_at, str)
            or len(created_at) > 64
            or "\x00" in created_at
        ):
            return (None, None, None)
        try:
            datetime.fromisoformat(created_at)
        except ValueError:
            return (None, None, None)
        dossier_sha256 = manifest.get("dossier_sha256")
        if not isinstance(dossier_sha256, str):
            return (None, None, None)
        return (session_id, created_at, dossier_sha256)

    def read_dossier(self, export_id: str) -> bytes | None:
        """Read one validated dossier by opaque inventory identity."""
        if not isinstance(export_id, str) or _SHA256.fullmatch(export_id) is None:
            return None
        inventory = _ExportInventory(_EXPORT_INVENTORY_LIMIT)
        try:
            root_details = self._exports.lstat()
        except OSError:
            return None
        if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
            return None
        try:
            root_fd = os.open(self._exports, _DIRECTORY_FLAGS)
        except OSError:
            return None
        try:
            opened_root = os.fstat(root_fd)
            if (opened_root.st_dev, opened_root.st_ino) != (
                root_details.st_dev,
                root_details.st_ino,
            ):
                return None
            for topic_name in inventory.names(root_fd):
                topic_fd, _ = self._open_directory(root_fd, topic_name)
                if topic_fd is None:
                    continue
                try:
                    for dossier_name in inventory.names(topic_fd):
                        if not hmac.compare_digest(
                            self._export_id(topic_name, dossier_name), export_id
                        ):
                            continue
                        directory_fd, _ = self._open_directory(topic_fd, dossier_name)
                        if directory_fd is None:
                            return None
                        try:
                            session_id, created_at, dossier_sha256, unavailable = (
                                self._manifest_summary(directory_fd)
                            )
                            if (
                                unavailable
                                or session_id is None
                                or created_at is None
                                or dossier_sha256 is None
                            ):
                                return None
                            return self._read_dossier_file(
                                directory_fd, dossier_sha256
                            )
                        finally:
                            os.close(directory_fd)
                finally:
                    os.close(topic_fd)
            return None
        finally:
            os.close(root_fd)

    @staticmethod
    def _read_dossier_file(directory_fd: int, expected_sha256: str) -> bytes | None:
        try:
            descriptor = os.open("dossier.md", _READ_FLAGS, dir_fd=directory_fd)
        except OSError:
            return None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_DOSSIER_BYTES:
                return None
            contents = os.read(descriptor, _MAX_DOSSIER_BYTES + 1)
            after = os.fstat(descriptor)
        except OSError:
            return None
        finally:
            os.close(descriptor)
        if (
            len(contents) != before.st_size
            or len(contents) > _MAX_DOSSIER_BYTES
            or ExportReader._file_identity(before) != ExportReader._file_identity(after)
            or hashlib.sha256(contents).hexdigest() != expected_sha256
        ):
            return None
        try:
            contents.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return contents

    @staticmethod
    def _export_id(topic_name: str, dossier_name: str) -> str:
        return hashlib.sha256(
            f"{topic_name}\x00{dossier_name}".encode()
        ).hexdigest()

    @staticmethod
    def _file_identity(details: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            details.st_dev,
            details.st_ino,
            details.st_size,
            details.st_mtime_ns,
            details.st_ctime_ns,
        )
