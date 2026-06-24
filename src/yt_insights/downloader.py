"""YouTube subtitle downloader via yt-dlp subprocess."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


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
