"""Dry-run-first acquisition planning and execution services."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .config import Config
from .downloader import (
    _READ_FLAGS,
    DownloadResult,
    VideoInfo,
    _confined_directory,
    _copy_regular_at,
    _list_regular_names,
    _promote_regular_file,
    _publish_new_regular_file,
    _read_regular_at,
    _replace_regular_file,
    download_subtitles,
)
from .paths import DataPaths

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SAFE_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_MAX_BATCH_BYTES = 1024 * 1024
_MAX_BATCH_LINES = 1000


class SourceKind(str, Enum):  # noqa: UP042 - preserve legacy str(Enum) semantics
    VIDEO = "video"
    PLAYLIST = "playlist"
    CHANNEL = "channel"
    BATCH = "batch"


class AcquisitionItemStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_PRESENT = "already_present"
    NO_TRANSCRIPT = "no_transcript"
    FAILED_RETRYABLE = "failed_retryable"


@dataclass(frozen=True, slots=True)
class AcquisitionItemReport:
    video_id: str
    status: AcquisitionItemStatus
    error_code: str | None = None
    source_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class IndexRefreshReport:
    catalog_published: bool
    search_published: bool


@dataclass(frozen=True)
class AcquisitionPlan:
    source: str
    source_kind: SourceKind
    output_root: Path
    transcripts_dir: Path
    insights_dir: Path
    data_paths: DataPaths
    selected_videos: tuple[VideoInfo, ...]
    selected_urls: tuple[str, ...]
    selected_count: int
    language: str
    analyze: bool
    requires_confirmation: bool
    exclusions: tuple[str, ...] = ()
    discovery_errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_kind": self.source_kind.value,
            "output_root": str(self.output_root),
            "selected_count": self.selected_count,
            "selected_urls": list(self.selected_urls),
            "language": self.language,
            "analyze": self.analyze,
            "requires_confirmation": self.requires_confirmation,
            "exclusions": list(self.exclusions),
            "discovery_errors": list(self.discovery_errors),
        }


@dataclass(frozen=True)
class AcquisitionReport:
    selected: int
    transcripts_ready: int
    insights_ready: int
    failures: tuple[str, ...]
    exclusions: tuple[str, ...] = ()
    items: tuple[AcquisitionItemReport, ...] = ()

    @property
    def exit_code(self) -> int:
        if self.selected and self.transcripts_ready == 0:
            return 1
        if self.failures or self.transcripts_ready < self.selected:
            return 4 if self.transcripts_ready else 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["failures"] = list(self.failures)
        payload["exclusions"] = list(self.exclusions)
        payload["items"] = [
            {
                "video_id": item.video_id,
                "status": item.status.value,
                "error_code": item.error_code,
                "source_sha256": item.source_sha256,
            }
            for item in self.items
        ]
        payload["exit_code"] = self.exit_code
        return payload


def classify_source(source: str) -> SourceKind:
    """Classify an explicit YouTube URL or an existing regular batch file."""
    if "\x00" in source:
        raise ValueError("source contains a NUL byte")
    candidate = Path(source).expanduser()
    if candidate.exists():
        if not stat.S_ISREG(candidate.lstat().st_mode):
            raise ValueError("batch source must be an existing regular file")
        return SourceKind.BATCH

    parsed = urlparse(source)
    if not parsed.scheme and not parsed.netloc:
        raise ValueError("batch source does not exist")
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("source must be an http(s) YouTube URL")
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)

    if host == "youtu.be":
        video_id = parsed.path.strip("/")
        if not _VIDEO_ID.fullmatch(video_id):
            raise ValueError("unsupported youtu.be URL")
        if "list" in query:
            raise ValueError("ambiguous YouTube URL contains video and playlist")
        return SourceKind.VIDEO

    if host not in _YOUTUBE_HOSTS:
        raise ValueError("unsupported source host")

    path = parsed.path.rstrip("/") or "/"
    video_values = query.get("v", [])
    playlist_values = query.get("list", [])
    if path == "/watch":
        has_video = len(video_values) == 1 and _VIDEO_ID.fullmatch(video_values[0])
        has_playlist = len(playlist_values) == 1 and bool(playlist_values[0])
        if has_video and has_playlist:
            raise ValueError("ambiguous YouTube URL contains video and playlist")
        if has_video:
            return SourceKind.VIDEO
        if has_playlist:
            return SourceKind.PLAYLIST
        raise ValueError("unsupported YouTube watch URL")
    if path == "/playlist" and len(playlist_values) == 1 and playlist_values[0]:
        return SourceKind.PLAYLIST
    if re.fullmatch(r"/(?:shorts|live)/[A-Za-z0-9_-]{11}", path):
        if playlist_values:
            raise ValueError("ambiguous YouTube URL contains video and playlist")
        return SourceKind.VIDEO
    if re.match(r"^/(?:@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)(?:/videos)?$", path):
        return SourceKind.CHANNEL
    raise ValueError("unsupported or ambiguous YouTube URL")


def read_batch_snapshot(path: Path) -> tuple[str, ...]:
    """Read one bounded, stable batch snapshot without following symlinks."""
    candidate = Path(path).expanduser().absolute()
    try:
        inventoried = candidate.lstat()
    except OSError as exc:
        raise ValueError("batch source is unavailable") from exc
    if not stat.S_ISREG(inventoried.st_mode):
        raise ValueError("batch source must be an existing regular file")
    if inventoried.st_size > _MAX_BATCH_BYTES:
        raise ValueError("batch source exceeds the byte limit")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise ValueError("platform cannot safely read batch files")
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (inventoried.st_dev, inventoried.st_ino)
            or opened.st_size > _MAX_BATCH_BYTES
        ):
            raise ValueError("batch source changed during inventory")
        chunks: list[bytes] = []
        remaining = _MAX_BATCH_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        final = os.fstat(descriptor)
        current_path = candidate.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if (
            len(encoded) > _MAX_BATCH_BYTES
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != identity
            or (current_path.st_dev, current_path.st_ino) != identity[:2]
        ):
            raise ValueError("batch source changed while being read")
    except OSError as exc:
        raise ValueError("batch source could not be read safely") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if b"\x00" in encoded:
        raise ValueError("batch source contains a NUL byte")
    try:
        text = encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("batch source must be UTF-8") from exc
    raw_lines = text.splitlines()
    if len(raw_lines) > _MAX_BATCH_LINES:
        raise ValueError("batch source exceeds the line limit")
    urls = tuple(line.strip() for line in raw_lines if line.strip())
    if not urls:
        raise ValueError("batch source contains no video URLs")
    for url in urls:
        try:
            kind = classify_source(url)
        except ValueError as exc:
            raise ValueError("batch source contains an invalid video URL") from exc
        if kind is not SourceKind.VIDEO:
            raise ValueError("batch source may contain only video URLs")
    return urls


def _video_id_from_url(source: str) -> str:
    parsed = urlparse(source)
    if (parsed.hostname or "").lower() == "youtu.be":
        return parsed.path.strip("/")
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0]
    return parsed.path.rstrip("/").rsplit("/", 1)[-1]


def _derive_slug(source: str, kind: SourceKind) -> str:
    if kind is SourceKind.BATCH:
        raw = Path(source).stem
    else:
        parsed = urlparse(source)
        if kind is SourceKind.PLAYLIST:
            raw = parse_qs(parsed.query).get("list", ["playlist"])[0]
        else:
            raw = parsed.path.strip("/").split("/")[0].removeprefix("@")
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return slug[:64].strip("-") or kind.value


def _validated_slug(slug: str) -> str:
    normalized = slug.strip().lower()
    if not _SAFE_SLUG.fullmatch(normalized):
        raise ValueError("slug must contain only lowercase letters, numbers and hyphens")
    return normalized


def _validate_confined_directory(path: Path, root: Path) -> None:
    """Require a lexical child of root with no existing symlink component."""
    absolute_root = root.expanduser().absolute()
    absolute_path = path.expanduser().absolute()
    try:
        root_details = absolute_root.lstat()
    except FileNotFoundError:
        root_details = None
    if root_details is not None and stat.S_ISLNK(root_details.st_mode):
        raise ValueError(f"data root contains a symlink: {absolute_root}")
    if root_details is not None and not stat.S_ISDIR(root_details.st_mode):
        raise ValueError(f"data root is not a directory: {absolute_root}")
    try:
        relative = absolute_path.relative_to(absolute_root)
    except ValueError as exc:
        raise ValueError(f"output path must remain under data root: {absolute_path}") from exc
    current = absolute_root
    for part in relative.parts:
        current = current / part
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode):
            raise ValueError(f"output path contains a symlink: {current}")
        if current == absolute_path and not stat.S_ISDIR(details.st_mode):
            raise ValueError(f"output path is not a directory: {current}")


def _validate_confined_file_target(path: Path, root: Path) -> None:
    _validate_confined_directory(path.parent, root)
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode):
        raise ValueError(f"output file is a symlink: {path}")
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f"output file is not regular: {path}")


def build_acquisition_plan(
    *,
    source: str,
    data_paths: DataPaths,
    slug: str | None = None,
    years: set[int] | None = None,
    language: str = "fr",
    analyze: bool = False,
    discovered: Iterable[VideoInfo] = (),
    discovery_errors: Iterable[str] = (),
    source_urls: Iterable[str] = (),
) -> AcquisitionPlan:
    """Build a deterministic, non-mutating plan from already discovered metadata."""
    kind = classify_source(source)
    normalized_language = language.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized_language):
        raise ValueError("language must contain only letters, numbers and hyphens")
    videos = list(discovered)
    snapshot_urls = tuple(source_urls)
    if kind is SourceKind.BATCH and not snapshot_urls:
        snapshot_urls = read_batch_snapshot(Path(source))
    if kind is SourceKind.VIDEO and not videos:
        video_id = _video_id_from_url(source)
        videos = [VideoInfo(video_id, video_id, "")]

    selected: list[VideoInfo] = []
    exclusions: list[str] = []
    seen_video_ids: set[str] = set()
    selected_years = years or set()
    for video in videos:
        if not _VIDEO_ID.fullmatch(video.video_id):
            exclusions.append(f"{video.video_id}: invalid_video_id")
            continue
        if video.video_id in seen_video_ids:
            exclusions.append(f"{video.video_id}: duplicate_video")
            continue
        seen_video_ids.add(video.video_id)
        if selected_years:
            if not re.fullmatch(r"\d{8}", video.upload_date):
                exclusions.append(f"{video.video_id}: missing_upload_date")
                continue
            if int(video.upload_date[:4]) not in selected_years:
                exclusions.append(f"{video.video_id}: year_not_selected")
                continue
        selected.append(video)

    multi_source = kind is not SourceKind.VIDEO
    if multi_source:
        safe_slug = _validated_slug(slug) if slug is not None else _derive_slug(source, kind)
        output_root = data_paths.root / safe_slug
        transcripts_dir = output_root / "transcripts"
        insights_dir = output_root / "insights"
    else:
        output_root = data_paths.root
        transcripts_dir = data_paths.transcripts
        insights_dir = data_paths.insights

    for path in (
        output_root,
        transcripts_dir,
        insights_dir,
        data_paths.catalog_database.parent,
        data_paths.search_database.parent,
    ):
        _validate_confined_directory(path, data_paths.root)
    _validate_confined_file_target(data_paths.catalog_database, data_paths.root)
    _validate_confined_file_target(data_paths.search_database, data_paths.root)
    source_url_by_id = {
        _video_id_from_url(url): url for url in snapshot_urls if classify_source(url) is SourceKind.VIDEO
    }

    return AcquisitionPlan(
        source=source,
        source_kind=kind,
        output_root=output_root,
        transcripts_dir=transcripts_dir,
        insights_dir=insights_dir,
        data_paths=data_paths,
        selected_videos=tuple(selected),
        selected_urls=tuple(source_url_by_id.get(video.video_id, video.watch_url) for video in selected),
        selected_count=len(selected),
        language=normalized_language,
        analyze=analyze,
        requires_confirmation=multi_source,
        exclusions=tuple(exclusions),
        discovery_errors=tuple(discovery_errors),
    )


@dataclass(frozen=True)
class _VttSnapshot:
    path: Path
    content: bytes


def _matching_vtts(
    directory: Path, video_id: str, language: str, root: Path
) -> list[_VttSnapshot]:
    suffix = f"[{video_id}].{language}.vtt"
    try:
        with _confined_directory(root, directory, create=False) as directory_fd:
            names = _list_regular_names(directory_fd, suffix=suffix, reject_unsafe=True)
            return [
                _VttSnapshot(directory / name, _read_regular_at(directory_fd, name))
                for name in names
            ]
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ValueError("cache changed while being read") from exc


def _matching_insight(vtt: _VttSnapshot, insights_dir: Path) -> Path:
    return insights_dir / f"{vtt.path.stem}.json"


def _safe_regular_file(path: Path, root: Path) -> bool:
    try:
        with _confined_directory(root, path.parent, create=False) as directory_fd:
            _read_regular_at(directory_fd, path.name)
            return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ValueError("cache changed while being read") from exc


def _require_directory_identity(root: Path, path: Path, expected_fd: int) -> None:
    """Require the public directory path to still name the held directory."""
    expected = os.fstat(expected_fd)
    try:
        with _confined_directory(root, path, create=False) as current_fd:
            current = os.fstat(current_fd)
    except (OSError, ValueError) as exc:
        raise ValueError(f"database parent changed before publication: {path}") from exc
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError(f"database parent changed before publication: {path}")


def _reject_nonempty_catalog_wal(parent_fd: int, database_name: str) -> None:
    wal_name = database_name + "-wal"
    try:
        details = os.stat(wal_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("catalog WAL is not a regular file")
    if details.st_size:
        raise ValueError("cannot refresh with a non-empty catalog WAL")


_PinnedIdentity = tuple[int, int, int, int, int]


@dataclass
class _PinnedRegularFile:
    descriptor: int
    name: str
    identity: _PinnedIdentity
    sha256: str

    def close(self) -> None:
        os.close(self.descriptor)


def _file_identity(details: os.stat_result) -> _PinnedIdentity:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


def _hash_pinned_descriptor(
    descriptor: int, expected: _PinnedIdentity, name: str
) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    if _file_identity(os.fstat(descriptor)) != expected:
        raise ValueError(f"pinned file changed while hashing: {name}")
    return digest.hexdigest()


def _open_pinned_regular(parent_fd: int, name: str) -> _PinnedRegularFile:
    if Path(name).name != name or "\x00" in name:
        raise ValueError("unsafe pinned file name")
    descriptor = os.open(name, _READ_FLAGS | os.O_NONBLOCK, dir_fd=parent_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"pinned file is not regular: {name}")
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"pinned file changed while opening: {name}")
        identity = _file_identity(opened)
        return _PinnedRegularFile(
            descriptor=descriptor,
            name=name,
            identity=identity,
            sha256=_hash_pinned_descriptor(descriptor, identity, name),
        )
    except BaseException:
        os.close(descriptor)
        raise


def _recheck_pinned_regular(parent_fd: int, pinned: _PinnedRegularFile) -> None:
    current = os.stat(pinned.name, dir_fd=parent_fd, follow_symlinks=False)
    if (current.st_dev, current.st_ino) != pinned.identity[:2]:
        raise ValueError(f"pinned receipt changed before database publication: {pinned.name}")
    if _file_identity(os.fstat(pinned.descriptor)) != pinned.identity:
        raise ValueError(f"pinned receipt changed before database publication: {pinned.name}")
    if (
        _hash_pinned_descriptor(pinned.descriptor, pinned.identity, pinned.name)
        != pinned.sha256
    ):
        raise ValueError(f"pinned receipt hash changed before database publication: {pinned.name}")
    final = os.stat(pinned.name, dir_fd=parent_fd, follow_symlinks=False)
    if (final.st_dev, final.st_ino) != pinned.identity[:2]:
        raise ValueError(f"pinned receipt changed before database publication: {pinned.name}")


def _copy_pinned_regular(pinned: _PinnedRegularFile, destination: Path) -> None:
    output_fd = os.open(
        destination,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.lseek(pinned.descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(pinned.descriptor, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                view = view[written:]
        os.fsync(output_fd)
    finally:
        os.close(output_fd)
    if (
        _hash_pinned_descriptor(pinned.descriptor, pinned.identity, pinned.name)
        != pinned.sha256
    ):
        destination.unlink(missing_ok=True)
        raise ValueError(f"pinned file changed while snapshotting: {pinned.name}")


def _remove_pinned_name(parent_fd: int, pinned: _PinnedRegularFile) -> None:
    try:
        current = os.stat(pinned.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == pinned.identity[:2]:
        os.unlink(pinned.name, dir_fd=parent_fd)
        os.fsync(parent_fd)


def _snapshot_valid_search_pair(
    parent_fd: int, database_name: str, destination: Path
) -> Path | None:
    """Snapshot and validate an existing public pair without opening it in SQLite."""
    from .search.sqlite_fts import SQLiteFtsIndex

    destination.mkdir()
    database = destination / database_name
    try:
        _copy_regular_at(parent_fd, database_name, database)
    except (FileNotFoundError, OSError, ValueError):
        return None
    receipt_prefix = f".{database_name}."
    for name in _list_regular_names(
        parent_fd, suffix=".receipt.json", reject_unsafe=False
    ):
        if not name.startswith(receipt_prefix):
            continue
        try:
            _copy_regular_at(parent_fd, name, destination / name)
        except (OSError, ValueError):
            continue
    try:
        SQLiteFtsIndex(database).status()
    except Exception:
        return None
    return database


def _validate_published_search_pair(
    parent_fd: int,
    database_name: str,
    expected_receipt: _PinnedRegularFile,
    destination: Path,
) -> None:
    """Reopen, snapshot and validate the just-published database/receipt pair."""
    from .search.sqlite_fts import SQLiteFtsIndex

    database = _open_pinned_regular(parent_fd, database_name)
    receipt = _open_pinned_regular(parent_fd, expected_receipt.name)
    try:
        if (
            receipt.identity != expected_receipt.identity
            or receipt.sha256 != expected_receipt.sha256
        ):
            raise ValueError("published search receipt changed")
        destination.mkdir()
        _copy_pinned_regular(database, destination / database_name)
        _copy_pinned_regular(receipt, destination / receipt.name)
    finally:
        receipt.close()
        database.close()
    SQLiteFtsIndex(destination / database_name).status()


def _remove_regular_at(parent_fd: int, name: str) -> None:
    """Remove one confined regular file and fsync its held parent directory."""
    try:
        details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(details.st_mode):
        raise ValueError(f"rollback target is not a regular file: {name}")
    os.unlink(name, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _require_absent_at(parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise RuntimeError(f"database rollback did not remove active file: {name}")


def rebuild_and_publish_indexes(data_paths: DataPaths) -> IndexRefreshReport:
    """Build both SQLite databases privately and publish via held parent dirfds."""
    from .catalog import Catalog, catalog_writer_lock
    from .search.corpus import scan_corpus
    from .search.sqlite_fts import SQLiteFtsIndex

    _validate_confined_file_target(data_paths.catalog_database, data_paths.root)
    _validate_confined_file_target(data_paths.search_database, data_paths.root)
    catalog_path = data_paths.catalog_database
    search_path = data_paths.search_database
    with (
        _confined_directory(
            data_paths.root, catalog_path.parent, create=True
        ) as catalog_parent_fd,
        _confined_directory(
            data_paths.root, search_path.parent, create=True
        ) as search_parent_fd,
        tempfile.TemporaryDirectory(prefix="yt-insights-indexes-") as staging_name,
    ):
        staging = Path(staging_name).resolve()
        catalog_build = staging / "catalog-build"
        catalog_build.mkdir()
        staged_catalog = catalog_build / catalog_path.name
        staged_search = staging / search_path.name

        with catalog_writer_lock(catalog_parent_fd, catalog_path.name):
            _reject_nonempty_catalog_wal(catalog_parent_fd, catalog_path.name)
            old_catalog_dir = staging / "old-catalog"
            old_catalog_dir.mkdir()
            old_catalog_candidate = old_catalog_dir / catalog_path.name
            old_catalog: Path | None = old_catalog_candidate
            try:
                _copy_regular_at(
                    catalog_parent_fd, catalog_path.name, old_catalog_candidate
                )
            except FileNotFoundError:
                old_catalog = None
            if old_catalog is not None:
                Catalog.validate_database(old_catalog)
                shutil.copyfile(old_catalog, staged_catalog)

            catalog = Catalog(staged_catalog)
            try:
                catalog.import_corpus(data_paths.root)
                catalog.checkpoint()
            finally:
                catalog.close()
            Catalog.validate_database(staged_catalog)

            manifest = scan_corpus(data_paths.root, limit=None)
            staged_index = SQLiteFtsIndex(staged_search)
            rebuilt = staged_index.rebuild(manifest)
            if staged_index.status() != rebuilt:
                raise RuntimeError("staged search index status does not match rebuild")
            receipt_prefix = f".{search_path.name}."
            receipt_paths = [
                candidate
                for candidate in staging.iterdir()
                if candidate.name.startswith(receipt_prefix)
                and candidate.name.endswith(".receipt.json")
                and candidate.is_file()
                and not candidate.is_symlink()
            ]
            if len(receipt_paths) != 1:
                raise RuntimeError("staged search index receipt is missing or ambiguous")
            receipt = receipt_paths[0]
            old_search = _snapshot_valid_search_pair(
                search_parent_fd, search_path.name, staging / "old-search"
            )

            _require_directory_identity(
                data_paths.root, catalog_path.parent, catalog_parent_fd
            )
            _reject_nonempty_catalog_wal(catalog_parent_fd, catalog_path.name)
            try:
                _replace_regular_file(
                    staged_catalog, catalog_parent_fd, catalog_path.name
                )
                _reject_nonempty_catalog_wal(catalog_parent_fd, catalog_path.name)
                published_catalog_dir = staging / "published-catalog"
                published_catalog_dir.mkdir()
                published_catalog = published_catalog_dir / catalog_path.name
                _copy_regular_at(
                    catalog_parent_fd, catalog_path.name, published_catalog
                )
                Catalog.validate_database(published_catalog)
                _reject_nonempty_catalog_wal(catalog_parent_fd, catalog_path.name)
            except Exception as exc:
                if old_catalog is not None:
                    _replace_regular_file(
                        old_catalog, catalog_parent_fd, catalog_path.name
                    )
                    restored_catalog_dir = staging / "restored-catalog"
                    restored_catalog_dir.mkdir()
                    restored_catalog = restored_catalog_dir / catalog_path.name
                    _copy_regular_at(
                        catalog_parent_fd, catalog_path.name, restored_catalog
                    )
                    Catalog.validate_database(restored_catalog)
                    _reject_nonempty_catalog_wal(
                        catalog_parent_fd, catalog_path.name
                    )
                else:
                    _remove_regular_at(catalog_parent_fd, catalog_path.name)
                    _require_absent_at(catalog_parent_fd, catalog_path.name)
                raise ValueError(
                    "catalog post-publication validation failed"
                ) from exc

            _require_directory_identity(
                data_paths.root, search_path.parent, search_parent_fd
            )
            pinned_receipt: _PinnedRegularFile | None = None
            database_published = False
            try:
                _publish_new_regular_file(receipt, search_parent_fd, receipt.name)
                pinned_receipt = _open_pinned_regular(
                    search_parent_fd, receipt.name
                )
                _require_directory_identity(
                    data_paths.root, search_path.parent, search_parent_fd
                )
                _replace_regular_file(
                    staged_search,
                    search_parent_fd,
                    search_path.name,
                    before_replace=lambda: _recheck_pinned_regular(
                        search_parent_fd, pinned_receipt
                    ),
                )
                database_published = True
                _validate_published_search_pair(
                    search_parent_fd,
                    search_path.name,
                    pinned_receipt,
                    staging / "published-search",
                )
            except Exception as exc:
                try:
                    if old_catalog is not None and old_search is not None:
                        _replace_regular_file(
                            old_catalog, catalog_parent_fd, catalog_path.name
                        )
                        if database_published:
                            _replace_regular_file(
                                old_search, search_parent_fd, search_path.name
                            )
                        restored_catalog_dir = staging / "restored-pair-catalog"
                        restored_catalog_dir.mkdir()
                        restored_catalog = restored_catalog_dir / catalog_path.name
                        _copy_regular_at(
                            catalog_parent_fd, catalog_path.name, restored_catalog
                        )
                        Catalog.validate_database(restored_catalog)
                        _reject_nonempty_catalog_wal(
                            catalog_parent_fd, catalog_path.name
                        )
                        if (
                            _snapshot_valid_search_pair(
                                search_parent_fd,
                                search_path.name,
                                staging / "restored-pair-search",
                            )
                            is None
                        ):
                            raise RuntimeError(
                                "catalog/search rollback validation failed"
                            ) from exc
                    else:
                        _remove_regular_at(catalog_parent_fd, catalog_path.name)
                        _require_absent_at(catalog_parent_fd, catalog_path.name)
                        if database_published:
                            _remove_regular_at(search_parent_fd, search_path.name)
                            _require_absent_at(search_parent_fd, search_path.name)
                finally:
                    if pinned_receipt is not None:
                        _remove_pinned_name(search_parent_fd, pinned_receipt)
                raise ValueError(
                    "search receipt/database publication validation failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            finally:
                if pinned_receipt is not None:
                    pinned_receipt.close()
    return IndexRefreshReport(catalog_published=True, search_published=True)


def execute_acquisition(
    plan: AcquisitionPlan,
    *,
    config: Config | None = None,
    cookies_from_browser: str | None = None,
    download: Callable[..., DownloadResult] | None = None,
    analyze_many: Callable[..., list[Any]] | None = None,
    backend_resolver: Callable[[Config], Any] | None = None,
    refresh_indexes: bool = True,
) -> AcquisitionReport:
    """Execute a confirmed plan using package services, preserving item identity."""
    if download is None:
        download = download_subtitles
    failures = [f"source ({plan.source}): {message}" for message in plan.discovery_errors]
    ready_by_id: dict[str, list[_VttSnapshot]] = {}
    items: list[AcquisitionItemReport] = []

    for video, url in zip(plan.selected_videos, plan.selected_urls, strict=False):
        try:
            cached = _matching_vtts(
                plan.transcripts_dir, video.video_id, plan.language, plan.data_paths.root
            )
        except ValueError as exc:
            failures.append(f"{video.video_id} ({video.title}): {exc}")
            items.append(
                AcquisitionItemReport(
                    video.video_id,
                    AcquisitionItemStatus.FAILED_RETRYABLE,
                    error_code="cache_read_failed",
                )
            )
            continue
        already_present = bool(cached)
        result = DownloadResult(vtt_files=[snapshot.path for snapshot in cached])
        if not cached:
            try:
                result = download(
                    url,
                    plan.transcripts_dir,
                    cookies_from_browser=cookies_from_browser,
                    sub_langs=plan.language,
                    data_root=plan.data_paths.root,
                )
            except Exception as exc:
                result = DownloadResult(errors=[f"{type(exc).__name__}: {exc}"], returncode=1)
        try:
            ready = _matching_vtts(
                plan.transcripts_dir, video.video_id, plan.language, plan.data_paths.root
            )
        except ValueError as exc:
            failures.append(f"{video.video_id} ({video.title}): {exc}")
            items.append(
                AcquisitionItemReport(
                    video.video_id,
                    AcquisitionItemStatus.FAILED_RETRYABLE,
                    error_code="cache_read_failed",
                )
            )
            continue
        if ready:
            ready_by_id[video.video_id] = ready
        diagnostics = list(result.errors)
        if result.returncode and not any("exited with status" in item for item in diagnostics):
            diagnostics.append(f"yt-dlp exited with status {result.returncode}")
        if diagnostics or not ready:
            details = "; ".join(diagnostics) or "no requested subtitle file was produced"
            failures.append(f"{video.video_id} ({video.title}): {details}")
        source_sha256 = hashlib.sha256(ready[0].content).hexdigest() if ready else None
        if already_present:
            status = AcquisitionItemStatus.ALREADY_PRESENT
            error_code = None
        elif result.returncode or result.errors:
            status = AcquisitionItemStatus.FAILED_RETRYABLE
            error_code = "download_failed"
        elif ready:
            status = AcquisitionItemStatus.ACQUIRED
            error_code = None
        else:
            status = AcquisitionItemStatus.NO_TRANSCRIPT
            error_code = "no_transcript"
        items.append(
            AcquisitionItemReport(
                video.video_id,
                status,
                error_code=error_code,
                source_sha256=source_sha256,
            )
        )

    all_vtts = [snapshot for snapshots in ready_by_id.values() for snapshot in snapshots]
    insights_ready = 0
    if plan.analyze and all_vtts:
        try:
            missing_insights = [
                snapshot
                for snapshot in all_vtts
                if not _safe_regular_file(
                    _matching_insight(snapshot, plan.insights_dir), plan.data_paths.root
                )
            ]
        except ValueError as exc:
            failures.append(f"analysis: {exc}")
            missing_insights = []
        if missing_insights:
            if analyze_many is None:
                from .analyzer import analyze_all

                analyze_many = analyze_all
            if backend_resolver is None:
                from .backends import resolve_backend

                backend_resolver = resolve_backend
            effective = config or Config(
                data_root=plan.data_paths.root,
                transcripts_dir=plan.transcripts_dir,
                insights_dir=plan.insights_dir,
            )
            backend: Any | None = None
            try:
                with _confined_directory(
                    plan.data_paths.root, plan.insights_dir, create=True
                ) as insights_fd, tempfile.TemporaryDirectory(
                    prefix="yt-insights-analysis-"
                ) as staging_name:
                    staging = Path(staging_name)
                    staged_vtts = staging / "transcripts"
                    staged_insights = staging / "insights"
                    staged_vtts.mkdir()
                    staged_insights.mkdir()
                    analyzer_inputs: list[Path] = []
                    for snapshot in missing_insights:
                        staged = staged_vtts / snapshot.path.name
                        staged.write_bytes(snapshot.content)
                        analyzer_inputs.append(staged)
                    backend = backend_resolver(effective)
                    analyze_many(
                        analyzer_inputs, staged_insights, backend, effective
                    )
                    for artifact in staged_insights.iterdir():
                        if artifact.suffix not in {".json", ".md"}:
                            continue
                        _promote_regular_file(
                            artifact, insights_fd, artifact.name
                        )
            except Exception as exc:
                failures.append(f"analysis: {type(exc).__name__}: {exc}")
            finally:
                if backend is not None:
                    backend.close()
        for video_id, paths in ready_by_id.items():
            try:
                ready_insight = any(
                    _safe_regular_file(
                        _matching_insight(snapshot, plan.insights_dir), plan.data_paths.root
                    )
                    for snapshot in paths
                )
            except ValueError as exc:
                title = next(v.title for v in plan.selected_videos if v.video_id == video_id)
                failures.append(f"{video_id} ({title}): {exc}")
                continue
            if ready_insight:
                insights_ready += 1
            else:
                title = next(v.title for v in plan.selected_videos if v.video_id == video_id)
                failures.append(f"{video_id} ({title}): insight was not produced")

    if refresh_indexes and all_vtts:
        try:
            rebuild_and_publish_indexes(plan.data_paths)
        except Exception as exc:
            failures.append(f"index: {type(exc).__name__}: {exc}")

    return AcquisitionReport(
        selected=plan.selected_count,
        transcripts_ready=len(ready_by_id),
        insights_ready=insights_ready,
        failures=tuple(failures),
        exclusions=plan.exclusions,
        items=tuple(items),
    )
