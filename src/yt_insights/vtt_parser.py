"""Timestamped VTT parser for YouTube Shorts suggestion pipeline.

Complements cleaner.py (which strips timestamps for insight extraction).
Here we preserve the first-occurrence timestamp of each unique text segment,
enabling LLMs to identify and reference precise time windows.

YouTube VTT specifics:
- Rolling captions: each sentence appears 2-3x in successive blocks as it scrolls.
- Inline word timestamps: <00:00:06.200><c> word</c> inside text lines.
- "Committed" tiny blocks (10ms duration) mark line transitions.

Strategy: strip all VTT markup, track first occurrence timestamp of each
unique text fragment. The resulting list is deduplicated and sorted by time.
"""

from __future__ import annotations

import re
from pathlib import Path


def ts_to_seconds(ts: str) -> float:
    """'01:23:45.678' or '01:23:45,678' -> 5025.678"""
    ts = ts.replace(",", ".")
    h, m, s = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def seconds_to_hms(secs: float) -> str:
    """5025.678 -> '01:23:45'"""
    secs = int(secs)
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


def _strip_markup(text: str) -> str:
    text = re.sub(r"<\d{2}:\d{2}:\d{2}[.,]\d{3}>", "", text)
    text = re.sub(r"</?c>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split()).strip()


def parse_vtt_timestamped(vtt_path: Path) -> list[dict]:
    """Parse a YouTube VTT file into deduplicated timestamped segments.

    Returns list of {'start': float (seconds), 'text': str}, sorted by start.
    Each unique text segment is captured at its first appearance timestamp.
    """
    content = vtt_path.read_text(encoding="utf-8")
    current_ts = 0.0
    seen: dict[str, float] = {}

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(("WEBVTT", "NOTE", "STYLE", "Kind:", "Language:")):
            continue
        if re.match(r"^\d+$", line):
            continue

        m = re.match(r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->", line)
        if m:
            current_ts = ts_to_seconds(m.group(1))
            continue

        clean = _strip_markup(line)
        if not clean or re.match(r"^\[.*?\]$", clean, re.IGNORECASE):
            continue

        if clean not in seen:
            seen[clean] = current_ts

    result = [{"start": ts, "text": txt} for txt, ts in seen.items()]
    result.sort(key=lambda x: x["start"])
    return result


def format_timestamped_transcript(segments: list[dict], max_chars: int = 18_000) -> str:
    """Format segments as '[HH:MM:SS] text' lines for LLM input.

    Truncates at max_chars to stay within context windows.
    """
    lines: list[str] = []
    total = 0
    for seg in segments:
        line = f"[{seconds_to_hms(seg['start'])}] {seg['text']}"
        total += len(line) + 1
        if total > max_chars:
            lines.append("[... transcript tronqué ...]")
            break
        lines.append(line)
    return "\n".join(lines)


def youtube_link(video_id: str, start_seconds: float) -> str:
    return f"https://youtube.com/watch?v={video_id}&t={int(start_seconds)}s"
