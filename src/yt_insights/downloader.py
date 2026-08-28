"""YouTube subtitle downloader via yt-dlp subprocess."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VideoInfo:
    video_id: str
    title: str
    upload_date: str  # YYYYMMDD or empty string

    @property
    def formatted_date(self) -> str:
        d = self.upload_date
        if len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d or "unknown"

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _source_args(source: str) -> list[str]:
    """Turn a SOURCE into yt-dlp positional args.

    A channel/playlist/video URL is passed as-is. A path to an existing local
    file (one URL per line) is passed via --batch-file, which is how yt-dlp
    reads a list of URLs. A bare positional file path is otherwise treated as a
    URL and rejected with "is not a valid URL".
    """
    if Path(source).is_file():
        return ["--batch-file", source]
    return [source]


@dataclass
class VideoListResult:
    videos: list[VideoInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    returncode: int = 0


def fetch_video_list(
    source: str, *, cookies_from_browser: str | None = None
) -> VideoListResult:
    """Fetch videos and retain yt-dlp failures for durable collection logs.

    Uses structured JSON from full metadata extraction so upload dates are exact.
    Media download remains disabled.
    """
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--skip-download",
        "--no-flat-playlist",
        "--ignore-errors",
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += _source_args(source)

    result = subprocess.run(cmd, capture_output=True, text=True)
    videos: list[VideoInfo] = []
    metadata_errors: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if line.count("|") >= 2:
                upload_date, remainder = line.split("|", 1)
                title, vid_id = remainder.rsplit("|", 1)
                payload = {
                    "upload_date": upload_date,
                    "title": title,
                    "id": vid_id,
                }
            else:
                metadata_errors.append("yt-dlp emitted an invalid metadata record")
                continue
        if not isinstance(payload, dict):
            metadata_errors.append("yt-dlp emitted an invalid metadata record")
            continue
        vid_id = payload.get("id")
        title = payload.get("title")
        upload_date = payload.get("upload_date")
        if not isinstance(vid_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid_id):
            metadata_errors.append("yt-dlp emitted metadata without a valid video id")
            continue
        videos.append(
            VideoInfo(
                video_id=vid_id,
                title=title if isinstance(title, str) else vid_id,
                upload_date=upload_date if isinstance(upload_date, str) else "",
            )
        )

    errors = metadata_errors + [
        line.strip()
        for line in result.stderr.splitlines()
        if "ERROR" in line.upper()
    ]
    if result.returncode != 0 and not errors:
        detail = result.stderr.strip() or "no diagnostic output"
        errors.append(f"yt-dlp exited with status {result.returncode}: {detail}")

    return VideoListResult(
        videos=videos,
        errors=errors,
        returncode=result.returncode,
    )


def _reject_symlink_output(path: Path) -> None:
    """Reject any existing symlink component or entry before yt-dlp writes."""
    absolute = path.expanduser().absolute()
    for component in reversed((absolute, *absolute.parents)):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(details.st_mode):
            raise ValueError(f"output path contains a symlink: {component}")
        if component == absolute and not stat.S_ISDIR(details.st_mode):
            raise ValueError(f"output path is not a directory: {component}")
    if absolute.is_dir():
        for entry in absolute.iterdir():
            if stat.S_ISLNK(entry.lstat().st_mode):
                raise ValueError(f"output directory contains a symlink: {entry.name}")


def list_videos(source: str, *, cookies_from_browser: str | None = None) -> list[VideoInfo]:
    """Compatibility wrapper returning only the discovered video list."""
    return fetch_video_list(
        source,
        cookies_from_browser=cookies_from_browser,
    ).videos


def vtt_to_video_info(vtt_path: Path) -> VideoInfo:
    """Parse a VTT filename into VideoInfo.

    Expected format: YYYYMMDD - Title [videoID].fr.vtt
    """
    stem = vtt_path.stem  # e.g. "20260223 - Title [nfupYzLjFGc].fr"
    stem = re.sub(r"\.(fr|en)$", "", stem)
    m_id = re.search(r"\[([A-Za-z0-9_-]+)\]$", stem)
    vid_id = m_id.group(1) if m_id else ""
    m_date = re.match(r"^(\d{8})\s*-\s*", stem)
    date = m_date.group(1) if m_date else ""
    title = re.sub(r"^\d{8}\s*-\s*", "", stem)
    title = re.sub(r"\s*\[[A-Za-z0-9_-]+\]$", "", title).strip()
    return VideoInfo(video_id=vid_id, title=title, upload_date=date)


@dataclass
class DownloadResult:
    vtt_files: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_count: int = 0
    returncode: int = 0


def download_subtitles(
    channel_url: str,
    output_dir: Path,
    *,
    sleep_requests: int = 0,
    cookies_from_browser: str | None = None,
    sub_langs: str = "fr,en",
) -> DownloadResult:
    """Download auto-generated subtitles from a YouTube channel/playlist/video.

    Detects new VTT files via a before/after snapshot of output_dir.
    (--print after_move:filepath only fires for media downloads, not subtitles.)
    """
    _reject_symlink_output(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _reject_symlink_output(output_dir)
    before = set(output_dir.glob("*.vtt"))

    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-langs", sub_langs,
        "--sub-format", "vtt",
        "--skip-download",
        "--write-info-json",
        "--no-write-playlist-metafiles",
        "--ignore-errors",
        "--output", str(output_dir / "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
        # Retry up to 5 times on extractor errors (e.g. 429), with exponential
        # backoff starting at 1s and capping at 30s between attempts.
        "--extractor-retries", "5",
        "--retry-sleep", "extractor:exp=1:30",
    ]
    if sleep_requests > 0:
        cmd += ["--sleep-requests", str(sleep_requests)]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += _source_args(channel_url)

    result = subprocess.run(cmd, capture_output=True, text=True)
    _reject_symlink_output(output_dir)

    vtt_files: list[Path] = []
    errors: list[str] = []
    skipped_count = 0

    # yt-dlp writes the "[info] Writing video subtitles to:" lines to stdout and
    # WARNING/ERROR lines to stderr, so both streams must be scanned.
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        line = line.strip()
        # yt-dlp logs written paths as "[info] Writing video subtitles to: <path>"
        # and skipped paths as "[info] <id>: Subtitle file already exists: <path>"
        if "Writing video subtitles to:" in line:
            path_str = line.split("Writing video subtitles to:", 1)[1].strip()
            p = Path(path_str)
            if p.suffix == ".vtt" and p.exists():
                vtt_files.append(p)
        elif "Subtitle file already exists:" in line:
            path_str = line.split("Subtitle file already exists:", 1)[1].strip()
            p = Path(path_str)
            if p.suffix == ".vtt" and p.exists():
                vtt_files.append(p)
                skipped_count += 1
        elif "ERROR" in line.upper():
            errors.append(line)

    vtt_files.extend(path for path in output_dir.glob("*.vtt") if path not in before)
    if result.returncode != 0 and not errors:
        detail = result.stderr.strip() or "no diagnostic output"
        errors.append(f"yt-dlp exited with status {result.returncode}: {detail}")

    return DownloadResult(
        vtt_files=sorted(set(vtt_files)),
        errors=errors,
        skipped_count=skipped_count,
        returncode=result.returncode,
    )
