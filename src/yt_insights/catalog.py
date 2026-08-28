"""Local SQLite catalog for discovered videos and imported corpus artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .cleaner import clean_vtt
from .downloader import VideoInfo, VideoListResult


_SCHEMA_VERSION = 1
_ARTIFACT_NAME = re.compile(
    r"^(?P<date>\d{8})\s*-\s*(?P<title>.*?)\s*"
    r"\[(?P<video_id>[A-Za-z0-9_-]{11})\]\."
    r"(?P<language>[A-Za-z0-9-]+)$"
)
_INSIGHT_KEYS = {"subject", "key_points", "tools", "advice", "quotes"}


@dataclass(frozen=True)
class RunSummary:
    run_id: int
    status: str
    items_seen: int
    items_written: int
    error_count: int


@dataclass(frozen=True)
class CatalogStats:
    videos: int
    sources: int
    artifacts: int
    runs: int
    errors: int


@dataclass(frozen=True)
class CollectionError:
    id: int
    run_id: int
    stage: str
    source: str
    item_ref: str
    message: str
    created_at: str


@dataclass(frozen=True)
class IngestionRun:
    id: int
    kind: str
    source: str
    started_at: str
    finished_at: str | None
    status: str
    items_seen: int
    items_written: int
    error_count: int


@dataclass(frozen=True)
class SearchResult:
    video_id: str
    title: str
    published_at: str | None
    sources: tuple[str, ...]
    watch_url: str
    highlight: str
    rank: float


@dataclass(frozen=True)
class _ArtifactName:
    published_at: str
    title: str
    video_id: str
    language: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _parse_artifact_name(path: Path) -> _ArtifactName:
    match = _ARTIFACT_NAME.match(path.stem)
    if match is None:
        raise ValueError(f"unsupported artifact filename: {path.name}")
    raw_date = match.group("date")
    return _ArtifactName(
        published_at=f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}",
        title=match.group("title").strip(),
        video_id=match.group("video_id"),
        language=match.group("language").lower(),
    )


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return " ".join(filter(None, (_flatten_text(item) for item in value.values())))
    if isinstance(value, list):
        return " ".join(filter(None, (_flatten_text(item) for item in value)))
    return str(value)


def _insight_validation_issues(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return ["root must be an object"]
    issues = [f"missing required key: {key}" for key in sorted(_INSIGHT_KEYS - data.keys())]
    if "subject" in data and not isinstance(data["subject"], str):
        issues.append("subject must be a string")
    for key in ("key_points", "tools", "advice", "quotes"):
        if key in data and not isinstance(data[key], list):
            issues.append(f"{key} must be a list")
    return issues


def _source_slug(source: str) -> str:
    handle = re.search(r"/@([^/?#]+)", source)
    if handle:
        return handle.group(1).lower()
    parsed = urlparse(source)
    candidate = Path(parsed.path).name if parsed.scheme else Path(source).stem
    if not candidate and parsed.netloc:
        candidate = parsed.netloc
    slug = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
    return slug or "unknown-source"


def _flat_artifact_source_slug(
    corpus_root: Path, path: Path, name: _ArtifactName
) -> str:
    """Resolve a stable source for inbox artifacts from yt-dlp metadata."""
    info_name = path.name.removesuffix(f".{name.language}{path.suffix}") + ".info.json"
    info_path = corpus_root / "transcripts" / info_name
    try:
        details = info_path.lstat()
        if not stat.S_ISREG(details.st_mode) or details.st_size > 1024 * 1024:
            return "inbox"
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "inbox"
    if not isinstance(payload, dict) or payload.get("id") != name.video_id:
        return "inbox"
    identity = payload.get("channel_id") or payload.get("uploader_id")
    if not isinstance(identity, str) or not identity.strip():
        return "inbox"
    return _source_slug(identity.strip())


class Catalog:
    """Own the catalog connection, schema, and idempotent domain operations."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 60000")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                version INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                published_at TEXT,
                watch_url TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                duration_seconds INTEGER,
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_sources (
                video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                source_slug TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (video_id, source_slug)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY,
                video_id TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
                source_slug TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('insight', 'transcript')),
                language TEXT NOT NULL,
                path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                searchable_text TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                UNIQUE (video_id, kind, language, sha256)
            );

            CREATE TABLE IF NOT EXISTS ingestion_runs (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                items_seen INTEGER NOT NULL DEFAULT 0,
                items_written INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS collection_errors (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES ingestion_runs(id) ON DELETE CASCADE,
                stage TEXT NOT NULL,
                source TEXT NOT NULL,
                item_ref TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS video_search USING fts5(
                video_id UNINDEXED,
                title,
                sources,
                subject,
                body,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        row = self._connection.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO schema_meta(version) VALUES (?)", (_SCHEMA_VERSION,)
            )
        elif row["version"] != _SCHEMA_VERSION:
            raise RuntimeError(
                f"Unsupported catalog schema {row['version']}; expected {_SCHEMA_VERSION}"
            )
        self._connection.commit()

    def import_corpus(self, root: Path) -> RunSummary:
        corpus_root = Path(root).resolve()
        if not corpus_root.is_dir():
            raise FileNotFoundError(f"Corpus directory does not exist: {corpus_root}")
        started_at = _utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO ingestion_runs(kind, source, started_at, status)
            VALUES ('corpus_import', ?, ?, 'running')
            """,
            (str(corpus_root), started_at),
        )
        run_id = int(cursor.lastrowid)
        self._connection.commit()
        items_seen = 0
        items_written = 0
        error_count = 0
        try:
            (
                items_seen,
                items_written,
                error_count,
                touched_video_ids,
            ) = self._import_corpus_items(corpus_root, run_id)
            for video_id in sorted(touched_video_ids):
                self._reindex_video(video_id)
        except BaseException as exc:
            self._connection.rollback()
            message = f"{type(exc).__name__}: {exc}"
            self._record_error(
                run_id=run_id,
                stage="corpus_import_run",
                source=str(corpus_root),
                item_ref="",
                message=message,
            )
            self._connection.execute(
                """
                UPDATE ingestion_runs
                SET finished_at = ?, status = 'failed', items_seen = ?,
                    items_written = 0, error_count = 1
                WHERE id = ?
                """,
                (_utc_now(), items_seen, run_id),
            )
            self._connection.commit()
            raise

        status = "partial" if error_count else "completed"

        self._connection.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = ?, status = ?, items_seen = ?,
                items_written = ?, error_count = ?
            WHERE id = ?
            """,
            (_utc_now(), status, items_seen, items_written, error_count, run_id),
        )
        self._connection.commit()
        return RunSummary(
            run_id=run_id,
            status=status,
            items_seen=items_seen,
            items_written=items_written,
            error_count=error_count,
        )

    def _import_corpus_items(
        self,
        corpus_root: Path,
        run_id: int,
    ) -> tuple[int, int, int, set[str]]:
        items_seen = 0
        items_written = 0
        error_count = 0
        touched_video_ids: set[str] = set()

        layouts: list[tuple[str, Path, Path]] = [
            ("inbox", corpus_root / "insights", corpus_root / "transcripts")
        ]
        layouts.extend(
            (source_dir.name, source_dir / "insights", source_dir / "transcripts")
            for source_dir in sorted(path for path in corpus_root.iterdir() if path.is_dir())
            if source_dir.name not in {"transcripts", "insights", "exports", "shorts", "clips", ".search"}
        )

        for source_slug, insights_dir, transcripts_dir in layouts:
            artifacts = [
                ("insight", path)
                for path in sorted(insights_dir.glob("*.json"))
                if not path.name.startswith("AGGREGATE_REPORT")
            ]
            artifacts += [
                ("transcript", path)
                for path in sorted(transcripts_dir.glob("*.vtt"))
            ]
            for kind, path in artifacts:
                items_seen += 1
                artifact_source_slug = source_slug
                try:
                    name = _parse_artifact_name(path)
                    artifact_source_slug = (
                        _flat_artifact_source_slug(corpus_root, path, name)
                        if source_slug == "inbox"
                        else source_slug
                    )
                    raw_bytes = path.read_bytes()
                    digest = hashlib.sha256(raw_bytes).hexdigest()
                    existing_artifact = self._connection.execute(
                        """
                        SELECT 1 FROM artifacts
                        WHERE video_id = ? AND kind = ? AND language = ? AND sha256 = ?
                        """,
                        (name.video_id, kind, name.language, digest),
                    ).fetchone()
                    previous_video = self._connection.execute(
                        "SELECT title, published_at FROM videos WHERE video_id = ?",
                        (name.video_id,),
                    ).fetchone()
                    previous_source = self._connection.execute(
                        """
                        SELECT 1 FROM video_sources
                        WHERE video_id = ? AND source_slug = ?
                        """,
                        (name.video_id, artifact_source_slug),
                    ).fetchone()

                    if kind == "insight":
                        data = json.loads(raw_bytes.decode("utf-8"))
                        validation_issues = _insight_validation_issues(data)
                        if validation_issues:
                            error_count += 1
                            self._record_error(
                                run_id=run_id,
                                stage="corpus_validation",
                                source=artifact_source_slug,
                                item_ref=str(path.resolve()),
                                message="; ".join(validation_issues),
                            )
                        searchable_text = (
                            _flatten_text(data) if existing_artifact is None else ""
                        )
                    else:
                        searchable_text = (
                            clean_vtt(path) if existing_artifact is None else ""
                        )

                    now = _utc_now()
                    self._upsert_video(name, now)
                    self._upsert_source(name.video_id, artifact_source_slug, now)
                    inserted_count = 0
                    if existing_artifact is None:
                        inserted = self._connection.execute(
                            """
                            INSERT OR IGNORE INTO artifacts(
                                video_id, source_slug, kind, language, path, sha256,
                                searchable_text, imported_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                name.video_id,
                                artifact_source_slug,
                                kind,
                                name.language,
                                str(path.resolve()),
                                digest,
                                searchable_text,
                                now,
                            ),
                        )
                        inserted_count = max(inserted.rowcount, 0)
                    items_written += inserted_count

                    video_changed = (
                        previous_video is None
                        or previous_video["title"] != name.title
                        or previous_video["published_at"] != name.published_at
                    )
                    if inserted_count or video_changed or previous_source is None:
                        touched_video_ids.add(name.video_id)
                except (OSError, UnicodeError, ValueError) as exc:
                    # Recover only from item/file defects. Database and programming
                    # errors must escape to the run-level rollback above.
                    error_count += 1
                    self._record_error(
                        run_id=run_id,
                        stage="corpus_import",
                        source=artifact_source_slug,
                        item_ref=str(path.resolve()),
                        message=f"{type(exc).__name__}: {exc}",
                    )

        return items_seen, items_written, error_count, touched_video_ids

    def ingest_discovery(
        self,
        source: str,
        result: VideoListResult,
    ) -> RunSummary:
        started_at = _utc_now()
        cursor = self._connection.execute(
            """
            INSERT INTO ingestion_runs(kind, source, started_at, status)
            VALUES ('discovery', ?, ?, 'running')
            """,
            (source, started_at),
        )
        run_id = int(cursor.lastrowid)
        slug = _source_slug(source)
        items_written = 0

        for video in result.videos:
            existed = self._connection.execute(
                "SELECT 1 FROM videos WHERE video_id = ?", (video.video_id,)
            ).fetchone()
            now = _utc_now()
            self._upsert_discovered_video(video, now)
            self._upsert_source(video.video_id, slug, now, source_url=source)
            self._reindex_video(video.video_id)
            if existed is None:
                items_written += 1

        for message in result.errors:
            self._record_error(
                run_id=run_id,
                stage="discovery",
                source=source,
                item_ref="",
                message=message,
            )

        if result.errors and not result.videos:
            status = "failed"
        elif result.errors:
            status = "partial"
        else:
            status = "completed"
        self._connection.execute(
            """
            UPDATE ingestion_runs
            SET finished_at = ?, status = ?, items_seen = ?,
                items_written = ?, error_count = ?
            WHERE id = ?
            """,
            (
                _utc_now(),
                status,
                len(result.videos),
                items_written,
                len(result.errors),
                run_id,
            ),
        )
        self._connection.commit()
        return RunSummary(
            run_id=run_id,
            status=status,
            items_seen=len(result.videos),
            items_written=items_written,
            error_count=len(result.errors),
        )

    def _upsert_video(self, name: _ArtifactName, now: str) -> None:
        self._upsert_video_values(
            video_id=name.video_id,
            title=name.title,
            published_at=name.published_at,
            now=now,
        )

    def _upsert_discovered_video(self, video: VideoInfo, now: str) -> None:
        raw_date = video.upload_date
        published_at = (
            f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if len(raw_date) == 8 and raw_date.isdigit()
            else None
        )
        self._upsert_video_values(
            video_id=video.video_id,
            title=video.title,
            published_at=published_at,
            now=now,
        )

    def _upsert_video_values(
        self,
        *,
        video_id: str,
        title: str,
        published_at: str | None,
        now: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO videos(
                video_id, title, published_at, watch_url, first_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                title = CASE WHEN excluded.title <> '' THEN excluded.title ELSE videos.title END,
                published_at = COALESCE(excluded.published_at, videos.published_at),
                updated_at = excluded.updated_at
            """,
            (
                video_id,
                title,
                published_at,
                f"https://www.youtube.com/watch?v={video_id}",
                now,
                now,
            ),
        )

    def _upsert_source(
        self,
        video_id: str,
        source_slug: str,
        now: str,
        *,
        source_url: str = "",
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO video_sources(
                video_id, source_slug, source_url, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(video_id, source_slug) DO UPDATE SET
                source_url = CASE
                    WHEN excluded.source_url <> '' THEN excluded.source_url
                    ELSE video_sources.source_url
                END,
                last_seen_at = excluded.last_seen_at
            """,
            (video_id, source_slug, source_url, now, now),
        )

    def _record_error(
        self,
        *,
        run_id: int,
        stage: str,
        source: str,
        item_ref: str,
        message: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO collection_errors(
                run_id, stage, source, item_ref, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, stage, source, item_ref, message, _utc_now()),
        )

    def _reindex_video(self, video_id: str) -> None:
        video = self._connection.execute(
            "SELECT title FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if video is None:
            return
        source_rows = self._connection.execute(
            """
            SELECT source_slug FROM video_sources
            WHERE video_id = ? ORDER BY source_slug
            """,
            (video_id,),
        ).fetchall()
        artifact_rows = self._connection.execute(
            """
            SELECT kind, searchable_text FROM artifacts
            WHERE video_id = ? ORDER BY kind, language, id
            """,
            (video_id,),
        ).fetchall()
        insight_text = " ".join(
            row["searchable_text"] for row in artifact_rows if row["kind"] == "insight"
        )
        body = " ".join(row["searchable_text"] for row in artifact_rows)
        sources = " ".join(row["source_slug"] for row in source_rows)

        self._connection.execute(
            "DELETE FROM video_search WHERE video_id = ?", (video_id,)
        )
        self._connection.execute(
            """
            INSERT INTO video_search(video_id, title, sources, subject, body)
            VALUES (?, ?, ?, ?, ?)
            """,
            (video_id, video["title"], sources, insight_text, body),
        )

    def list_errors(self, *, run_id: int | None = None) -> list[CollectionError]:
        sql = "SELECT * FROM collection_errors"
        parameters: tuple[object, ...] = ()
        if run_id is not None:
            sql += " WHERE run_id = ?"
            parameters = (run_id,)
        sql += " ORDER BY id"
        return [
            CollectionError(
                id=int(row["id"]),
                run_id=int(row["run_id"]),
                stage=row["stage"],
                source=row["source"],
                item_ref=row["item_ref"],
                message=row["message"],
                created_at=row["created_at"],
            )
            for row in self._connection.execute(sql, parameters)
        ]

    def list_runs(self) -> list[IngestionRun]:
        return [
            IngestionRun(
                id=int(row["id"]),
                kind=row["kind"],
                source=row["source"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                status=row["status"],
                items_seen=int(row["items_seen"]),
                items_written=int(row["items_written"]),
                error_count=int(row["error_count"]),
            )
            for row in self._connection.execute(
                "SELECT * FROM ingestion_runs ORDER BY id"
            )
        ]

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        limit: int = 20,
    ) -> list[SearchResult]:
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        if not tokens or limit <= 0:
            return []
        match_query = " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

        sql = """
            SELECT
                video_search.video_id,
                videos.title,
                videos.published_at,
                videos.watch_url,
                snippet(video_search, 4, '[', ']', ' … ', 18) AS highlight,
                bm25(video_search) AS rank
            FROM video_search
            JOIN videos ON videos.video_id = video_search.video_id
            WHERE video_search MATCH ?
        """
        parameters: list[object] = [match_query]
        if source is not None:
            sql += """
                AND EXISTS (
                    SELECT 1 FROM video_sources
                    WHERE video_sources.video_id = videos.video_id
                      AND video_sources.source_slug = ?
                )
            """
            parameters.append(source)
        sql += " ORDER BY rank, COALESCE(videos.published_at, '') DESC, videos.video_id LIMIT ?"
        parameters.append(limit)

        results: list[SearchResult] = []
        for row in self._connection.execute(sql, parameters):
            sources = tuple(
                source_row["source_slug"]
                for source_row in self._connection.execute(
                    """
                    SELECT source_slug FROM video_sources
                    WHERE video_id = ? ORDER BY source_slug
                    """,
                    (row["video_id"],),
                )
            )
            results.append(
                SearchResult(
                    video_id=row["video_id"],
                    title=row["title"],
                    published_at=row["published_at"],
                    sources=sources,
                    watch_url=row["watch_url"],
                    highlight=row["highlight"] or "",
                    rank=float(row["rank"]),
                )
            )
        return results

    def stats(self) -> CatalogStats:
        def count(table: str) -> int:
            row = self._connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            return int(row["n"])

        return CatalogStats(
            videos=count("videos"),
            sources=count("video_sources"),
            artifacts=count("artifacts"),
            runs=count("ingestion_runs"),
            errors=count("collection_errors"),
        )
