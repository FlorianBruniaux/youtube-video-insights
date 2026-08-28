"""YouTube subtitle downloader via yt-dlp subprocess."""

from __future__ import annotations

import re
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

    Uses --flat-playlist so yt-dlp only queries the playlist API, no media fetch.
    """
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(upload_date)s|%(title)s|%(id)s",
        "--ignore-errors",
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += _source_args(source)

    result = subprocess.run(cmd, capture_output=True, text=True)
    videos: list[VideoInfo] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.count("|") < 2:
            continue
        date, title, vid_id = line.split("|", 2)
        if vid_id:
            videos.append(VideoInfo(video_id=vid_id, title=title, upload_date=date or ""))

    errors = [
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
    output_dir.mkdir(parents=True, exist_ok=True)

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

    output_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(cmd, capture_output=True, text=True)

    vtt_files: list[Path] = []
    errors: list[str] = []

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
        elif "ERROR" in line.upper():
            errors.append(line)

    return DownloadResult(
        vtt_files=sorted(set(vtt_files)),
        errors=errors,
        skipped_count=0,
    )
