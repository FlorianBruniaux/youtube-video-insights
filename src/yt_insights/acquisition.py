"""Dry-run-first acquisition planning and execution services."""

from __future__ import annotations

import re
import stat
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

from .config import Config
from .downloader import DownloadResult, VideoInfo, download_subtitles
from .paths import DataPaths


_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_SAFE_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?")
_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}


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
) -> AcquisitionPlan:
    """Build a deterministic, non-mutating plan from already discovered metadata."""
    kind = classify_source(source)
    normalized_language = language.strip().lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized_language):
        raise ValueError("language must contain only letters, numbers and hyphens")
    videos = list(discovered)
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

    return AcquisitionPlan(
        source=source,
        source_kind=kind,
        output_root=output_root,
        transcripts_dir=transcripts_dir,
        insights_dir=insights_dir,
        data_paths=data_paths,
        selected_videos=tuple(selected),
        selected_urls=tuple(video.watch_url for video in selected),
        selected_count=len(selected),
        language=normalized_language,
        analyze=analyze,
        requires_confirmation=multi_source,
        exclusions=tuple(exclusions),
        discovery_errors=tuple(discovery_errors),
    )


def _matching_vtts(directory: Path, video_id: str, language: str) -> list[Path]:
    if not directory.is_dir():
        return []
    suffix = f"[{video_id}].{language}.vtt"
    return sorted(path for path in directory.glob("*.vtt") if path.name.endswith(suffix))


def _matching_insight(vtt: Path, insights_dir: Path) -> Path:
    return insights_dir / f"{vtt.stem}.json"


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
    ready_by_id: dict[str, list[Path]] = {}

    for video, url in zip(plan.selected_videos, plan.selected_urls):
        cached = _matching_vtts(plan.transcripts_dir, video.video_id, plan.language)
        result = DownloadResult(vtt_files=cached)
        if not cached:
            try:
                result = download(
                    url,
                    plan.transcripts_dir,
                    cookies_from_browser=cookies_from_browser,
                    sub_langs=plan.language,
                )
            except Exception as exc:
                result = DownloadResult(errors=[f"{type(exc).__name__}: {exc}"], returncode=1)
        ready = _matching_vtts(plan.transcripts_dir, video.video_id, plan.language)
        if not ready:
            ready = [
                path
                for path in result.vtt_files
                if path.name.endswith(f"[{video.video_id}].{plan.language}.vtt") and path.exists()
            ]
        if ready:
            ready_by_id[video.video_id] = sorted(set(ready))
        else:
            details = "; ".join(result.errors) or "no requested subtitle file was produced"
            failures.append(f"{video.video_id} ({video.title}): {details}")

    all_vtts = [path for paths in ready_by_id.values() for path in paths]
    insights_ready = 0
    if plan.analyze and all_vtts:
        missing_insights = [
            path for path in all_vtts if not _matching_insight(path, plan.insights_dir).is_file()
        ]
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
                backend = backend_resolver(effective)
                analyze_many(missing_insights, plan.insights_dir, backend, effective)
            except Exception as exc:
                failures.append(f"analysis: {type(exc).__name__}: {exc}")
            finally:
                if backend is not None:
                    backend.close()
        for video_id, paths in ready_by_id.items():
            if any(_matching_insight(path, plan.insights_dir).is_file() for path in paths):
                insights_ready += 1
            else:
                title = next(v.title for v in plan.selected_videos if v.video_id == video_id)
                failures.append(f"{video_id} ({title}): insight was not produced")

    if refresh_indexes and all_vtts:
        try:
            from .catalog import Catalog
            from .search.corpus import scan_corpus
            from .search.sqlite_fts import SQLiteFtsIndex

            with Catalog(plan.data_paths.catalog_database) as catalog:
                catalog.import_corpus(plan.data_paths.root)
            SQLiteFtsIndex(plan.data_paths.search_database).rebuild(
                scan_corpus(plan.data_paths.root)
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
