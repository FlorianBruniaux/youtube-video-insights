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


def list_videos(source: str) -> list[VideoInfo]:
    """Fetch video metadata from a channel/playlist without downloading anything.

    Uses --flat-playlist so yt-dlp only queries the playlist API, no media fetch.
    """
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--print", "%(upload_date)s|%(title)s|%(id)s",
        "--ignore-errors",
        source,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    videos: list[VideoInfo] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.count("|") < 2:
            continue
        date, title, vid_id = line.split("|", 2)
        if vid_id:
            videos.append(VideoInfo(video_id=vid_id, title=title, upload_date=date or ""))
    return videos


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
) -> DownloadResult:
    """Download auto-generated subtitles from a YouTube channel/playlist/video.

    Uses --print after_move:filepath to get the exact list of files written
    without a post-run glob.  Only .vtt files are collected; other yt-dlp
    output lines (info JSON paths, etc.) are silently ignored.

    yt-dlp command is verbatim from the POC (boldguy/scripts/youtube_insights.py:76-85)
    with the addition of --print and optional --sleep-requests.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-langs", "fr,en",
        "--sub-format", "vtt",
        "--skip-download",
        "--ignore-errors",
        "--print", "after_move:filepath",
        "--output", str(output_dir / "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
    ]
    if sleep_requests > 0:
        cmd += ["--sleep-requests", str(sleep_requests)]
    cmd.append(channel_url)

    result = subprocess.run(cmd, capture_output=True, text=True)

    vtt_files: list[Path] = []
    errors: list[str] = []
    skipped = 0

    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        p = Path(line)
        if p.suffix == ".vtt":
            if p.exists():
                vtt_files.append(p)
            else:
                skipped += 1
        # Non-.vtt lines (info JSON, thumbnails) are ignored

    for line in result.stderr.splitlines():
        line = line.strip()
        if line and "ERROR" in line.upper():
            errors.append(line)

    return DownloadResult(
        vtt_files=sorted(vtt_files),
        errors=errors,
        skipped_count=skipped,
    )
