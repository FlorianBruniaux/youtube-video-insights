"""Bounded, path-free projections for the local web application."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from typing import Final

from yt_insights.catalog import Catalog, CatalogError, ReadOnlyCatalog

_MAX_PAGE_SIZE: Final = 100
_MAX_METADATA_VALUES: Final = 20
_MAX_MANIFEST_BYTES: Final = 64 * 1024
# Export roots are user-managed directories. These caps bound descriptor work
# and retained names even if the caller asks for a one-record page.
_MAX_EXPORT_TOPICS: Final = 32
_MAX_DOSSIERS_PER_TOPIC: Final = 32
_DIRECTORY_FLAGS: Final = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS: Final = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
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
_LANGUAGE: Final = re.compile(r"[A-Za-z0-9-]{1,32}")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


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

    def __init__(self, catalog_database: Path) -> None:
        self._catalog_database = Path(catalog_database)
        if not self._catalog_database.is_absolute():
            raise ValueError("catalog database path must be absolute")

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
                items = [
                    self._source_item(catalog, connection, row)
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
        catalog: ReadOnlyCatalog, connection: sqlite3.Connection, row: sqlite3.Row
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
        }


class ExportReader:
    """Read safe summaries of locally generated research dossiers."""

    def __init__(self, exports: Path) -> None:
        self._exports = Path(exports)
        if not self._exports.is_absolute():
            raise ValueError("exports path must be absolute")

    def list_exports(self, *, limit: int) -> dict[str, object]:
        """Return safe dossiers from the configured topic/dossier hierarchy."""
        _require_page(limit, 0)
        try:
            root_details = self._exports.lstat()
        except FileNotFoundError:
            return {"items": [], "limit": limit, "truncated": False}
        if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
            return {"items": [], "limit": limit, "truncated": False}
        try:
            root_fd = os.open(self._exports, _DIRECTORY_FLAGS)
        except OSError:
            return {"items": [], "limit": limit, "truncated": False}
        try:
            opened_root = os.fstat(root_fd)
            if (opened_root.st_dev, opened_root.st_ino) != (
                root_details.st_dev,
                root_details.st_ino,
            ):
                return {"items": [], "limit": limit, "truncated": False}
            items: list[dict[str, object]] = []
            topic_names, truncated = self._bounded_names(
                root_fd, maximum=_MAX_EXPORT_TOPICS
            )
            for topic_index, topic_name in enumerate(topic_names):
                topic_fd = self._open_directory(root_fd, topic_name)
                if topic_fd is None:
                    continue
                try:
                    dossier_names, dossiers_truncated = self._bounded_names(
                        topic_fd, maximum=_MAX_DOSSIERS_PER_TOPIC
                    )
                    truncated = truncated or dossiers_truncated
                    for dossier_index, dossier_name in enumerate(dossier_names):
                        item = self._export_item(topic_fd, dossier_name)
                        if item is None:
                            continue
                        items.append(item)
                        has_unread_names = (
                            dossier_index < len(dossier_names) - 1
                            or topic_index < len(topic_names) - 1
                            or truncated
                        )
                        if len(items) >= limit and has_unread_names:
                            return {
                                "items": items,
                                "limit": limit,
                                "truncated": True,
                            }
                finally:
                    os.close(topic_fd)
            return {"items": items, "limit": limit, "truncated": truncated}
        finally:
            os.close(root_fd)

    @staticmethod
    def _bounded_names(directory_fd: int, *, maximum: int) -> tuple[list[str], bool]:
        """Retain no more than one fixed inventory page of direct children."""
        names: list[str] = []
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if len(names) >= maximum:
                        return (sorted(names), True)
                    names.append(entry.name)
        except OSError:
            return ([], True)
        return (sorted(names), False)

    @staticmethod
    def _open_directory(parent_fd: int, name: str) -> int | None:
        if Path(name).name != name or name in {"", ".", ".."} or "\x00" in name:
            return None
        try:
            details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            return None
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            return None
        try:
            directory_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError:
            return None
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            os.close(directory_fd)
            return None
        return directory_fd

    @staticmethod
    def _export_item(parent_fd: int, name: str) -> dict[str, object] | None:
        directory_fd = ExportReader._open_directory(parent_fd, name)
        if directory_fd is None:
            return None
        try:
            session_id, created_at = ExportReader._manifest_summary(directory_fd)
        finally:
            os.close(directory_fd)
        manifest_valid = session_id is not None and created_at is not None
        return {
            "name": name,
            "session_id": session_id if manifest_valid else None,
            "created_at": created_at if manifest_valid else None,
            "manifest_valid": manifest_valid,
        }

    @staticmethod
    def _manifest_summary(directory_fd: int) -> tuple[str | None, str | None]:
        try:
            details = os.stat("manifest.json", dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            return (None, None)
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_size > _MAX_MANIFEST_BYTES
        ):
            return (None, None)
        try:
            manifest_fd = os.open("manifest.json", _READ_FLAGS, dir_fd=directory_fd)
        except OSError:
            return (None, None)
        try:
            opened = os.fstat(manifest_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
            ):
                return (None, None)
            contents = os.read(manifest_fd, _MAX_MANIFEST_BYTES + 1)
        finally:
            os.close(manifest_fd)
        if len(contents) > _MAX_MANIFEST_BYTES:
            return (None, None)
        try:
            manifest = json.loads(contents.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (None, None)
        return ExportReader._validate_manifest(manifest)

    @staticmethod
    def _validate_manifest(manifest: object) -> tuple[str | None, str | None]:
        if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_FIELDS:
            return (None, None)
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
            return (None, None)
        session = manifest.get("session")
        if not isinstance(session, dict):
            return (None, None)
        session_id = session.get("session_id")
        created_at = session.get("created_at")
        if (
            not isinstance(session_id, str)
            or _SESSION_ID.fullmatch(session_id) is None
            or not isinstance(created_at, str)
            or len(created_at) > 64
            or "\x00" in created_at
        ):
            return (None, None)
        try:
            datetime.fromisoformat(created_at)
        except ValueError:
            return (None, None)
        return (session_id, created_at)
