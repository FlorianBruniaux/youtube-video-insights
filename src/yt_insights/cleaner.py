"""VTT subtitle cleaning and title parsing.

Logic ported verbatim from boldguy/scripts/youtube_insights.py (lines 92-126, 222-229).
Tested on 47 real YouTube VTT files — do not modify the dedup logic.
"""

from __future__ import annotations

import re
from pathlib import Path


def clean_vtt(vtt_path: Path) -> str:
    """Extract plain text from a VTT file.

    YouTube VTT has overlapping captions (each line appears 2-3x as it scrolls),
    so we deduplicate while preserving order.
    """
    content = vtt_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    seen: set[str] = set()
    result: list[str] = []

    for line in lines:
        line = line.strip()

        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
            continue
        # Timing lines: 00:00:04.000 --> 00:00:06.000
        if re.match(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->", line):
            continue
        # Sequence numbers
        if re.match(r"^\d+$", line):
            continue
        # Remove inline HTML tags (<c>, <00:00:04.000>, etc.)
        line = re.sub(r"<[^>]+>", "", line).strip()
        # Skip sound/music annotations like [Musique]
        if re.match(r"^\[.*\]$", line, re.IGNORECASE):
            continue

        if line and line not in seen:
            seen.add(line)
            result.append(line)

    return " ".join(result)


def parse_title(vtt_path: Path) -> str:
    """Strip language suffix and video ID from filename."""
    name = vtt_path.stem
    # Remove .fr or .en suffix added by yt-dlp
    name = re.sub(r"\.(fr|en)$", "", name)
    # Remove YouTube video ID [xxxxxxxxxxx] at the end
    name = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", name)
    return name.strip()
