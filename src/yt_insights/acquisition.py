"""Dry-run-first acquisition planning and execution services."""

from __future__ import annotations

import re
import stat
import os
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

from .config import Config
from .downloader import (
    DownloadResult,
    VideoInfo,
    _confined_directory,
    _list_regular_names,
    _promote_regular_file,
    _read_regular_at,
    download_subtitles,
)
from .paths import DataPaths


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SAFE_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_MAX_BATCH_BYTES = 1024 * 1024
_MAX_BATCH_LINES = 1000


class SourceKind(str, Enum):
    VIDEO = "video"
    PLAYLIST = "playlist"
    CHANNEL = "channel"
    BATCH = "batch"


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

    for video, url in zip(plan.selected_videos, plan.selected_urls):
        try:
            cached = _matching_vtts(
                plan.transcripts_dir, video.video_id, plan.language, plan.data_paths.root
            )
        except ValueError as exc:
            failures.append(f"{video.video_id} ({video.title}): {exc}")
            continue
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
            continue
        if ready:
            ready_by_id[video.video_id] = ready
        diagnostics = list(result.errors)
        if result.returncode and not any("exited with status" in item for item in diagnostics):
            diagnostics.append(f"yt-dlp exited with status {result.returncode}")
        if diagnostics or not ready:
            details = "; ".join(diagnostics) or "no requested subtitle file was produced"
            failures.append(f"{video.video_id} ({video.title}): {details}")

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
                ) as insights_fd:
                    with tempfile.TemporaryDirectory(
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
            from .catalog import Catalog
            from .search.corpus import scan_corpus
            from .search.sqlite_fts import SQLiteFtsIndex

            _validate_confined_file_target(
                plan.data_paths.catalog_database, plan.data_paths.root
            )
            _validate_confined_file_target(
                plan.data_paths.search_database, plan.data_paths.root
            )
            with Catalog(plan.data_paths.catalog_database) as catalog:
                catalog.import_corpus(plan.data_paths.root)
            SQLiteFtsIndex(plan.data_paths.search_database).rebuild(
                scan_corpus(plan.data_paths.root, limit=None)
            )
        except Exception as exc:
            failures.append(f"index: {type(exc).__name__}: {exc}")

    return AcquisitionReport(
        selected=plan.selected_count,
        transcripts_ready=len(ready_by_id),
        insights_ready=insights_ready,
        failures=tuple(failures),
        exclusions=plan.exclusions,
    )
