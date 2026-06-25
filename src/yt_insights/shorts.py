"""YouTube Shorts suggestion pipeline.

For each VTT transcript, identify the top 3 moments (30-90s) that would make
a good Short: autonomous hook, punchy verbatim, clean in/out points.

Storage layout (under config.shorts_dir):
  {vtt_stem}.json   — raw suggestion list (cache, atomic write)
  {vtt_stem}.md     — human-readable markdown version
  INDEX.md          — global index sorted by score across all talks

Idempotency: existing .json files are read from cache; pass force=True to re-run.
stop_reason gate: max_tokens responses are NOT cached (likely truncated JSON).

Clip download (optional, Phase 2):
  generate_short_clip() calls yt-dlp --download-sections to fetch only the
  relevant segment from YouTube, no full-video download needed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .backends import LLMBackend, backend_type
from .cleaner import parse_title
from .config import Config, effective_concurrency
from .vtt_parser import (
    format_timestamped_transcript,
    parse_vtt_timestamped,
    seconds_to_hms,
    ts_to_seconds,
    youtube_link,
)

_VIDEO_ID_RE = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
_DATE_PREFIX_RE = re.compile(r"^\d{8}\s*-\s*")

SELECTION_CRITERIA = """Un bon YouTube Short (30-90 secondes) doit :
1. Être autonome : se comprendre sans les 5 minutes d'avant.
2. Contenir un hook contre-intuitif ou une tension forte qui arrête le scroll.
3. Avoir un verbatim punchy : formule mémorable, pas une explication en 3 parties.
4. Avoir des bornes nettes : début sur une amorce forte, fin sur une chute ou un point.
5. Durer entre 30 et 90 secondes.
6. Contenir si possible un chiffre ou une preuve concrète.

À rejeter : intros/remerciements, transitions logistiques, private jokes de salle,
monologues techniques de plus de 90s sans punchline."""


@dataclass
class ShortSuggestion:
    start: str
    end: str
    score: int
    hook: str
    verbatim: str
    rationale: str

    @property
    def start_seconds(self) -> float:
        return ts_to_seconds(self.start + ".000" if "." not in self.start else self.start)

    @property
    def end_seconds(self) -> float:
        return ts_to_seconds(self.end + ".000" if "." not in self.end else self.end)

    @property
    def duration(self) -> int:
        return max(0, int(self.end_seconds - self.start_seconds))

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "score": self.score,
            "hook": self.hook,
            "verbatim": self.verbatim,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ShortSuggestion":
        return cls(
            start=d.get("start", "00:00:00"),
            end=d.get("end", "00:00:30"),
            score=int(d.get("score", 3)),
            hook=d.get("hook", ""),
            verbatim=d.get("verbatim", ""),
            rationale=d.get("rationale", ""),
        )


@dataclass
class ShortsResult:
    title: str
    vtt_path: Path
    video_id: str | None
    json_path: Path
    suggestions: list[ShortSuggestion] = field(default_factory=list)

    def to_dict(self) -> list[dict]:
        return [s.to_dict() for s in self.suggestions]

    def to_markdown(self) -> str:
        yt_base = f"https://youtube.com/watch?v={self.video_id}" if self.video_id else None
        lines = [f"# Suggestions Shorts — {self.title}\n"]
        if self.video_id:
            lines.append(f"**Video ID :** {self.video_id}")
            lines.append(f"**Lien :** {yt_base}\n")
        lines.append("---\n")

        if not self.suggestions:
            lines.append("*Aucun moment satisfaisant les critères identifié.*\n")
            return "\n".join(lines)

        for i, s in enumerate(self.suggestions, 1):
            direct_link = youtube_link(self.video_id, s.start_seconds) if self.video_id else ""
            lines.append(f"## Short {i} — Score : {s.score}/5\n")
            lines.append(f"**Timestamps :** {s.start} -> {s.end} ({s.duration}s)")
            if direct_link:
                lines.append(f"**Lien direct :** {direct_link}")
            lines.append(f"**Hook :** {s.hook}")
            lines.append(f"**Rationale :** {s.rationale}\n")
            if s.verbatim:
                lines.append(f"> {s.verbatim.strip()}\n")
            lines.append("---\n")

        return "\n".join(lines)


def _extract_video_id(vtt_path: Path) -> str | None:
    m = _VIDEO_ID_RE.search(vtt_path.stem)
    return m.group(1) if m else None


def _parse_short_title(vtt_path: Path) -> str:
    name = parse_title(vtt_path)
    return _DATE_PREFIX_RE.sub("", name).strip()


def _build_prompt(title: str, transcript: str, insight_md: str) -> str:
    insight_section = ""
    if insight_md:
        insight_section = f"\n=== INSIGHTS EXTRAITS DE CE TALK ===\n{insight_md[:3000]}\n"

    return f"""Tu es un expert en sélection de contenu YouTube Shorts.

Voici la transcription horodatée d'un talk de la chaîne "Dev With AI" :
Titre : "{title}"

=== TRANSCRIPT HORODATÉ ===
{transcript}
{insight_section}
=== CRITÈRES ===
{SELECTION_CRITERIA}

=== TÂCHE ===
Identifie les 3 meilleurs moments pour un YouTube Short.
Utilise UNIQUEMENT des timestamps présents dans le transcript.
Durée entre start et end : 30 à 90 secondes.

Réponds en JSON strict, sans texte avant ni après :
[
  {{
    "start": "HH:MM:SS",
    "end": "HH:MM:SS",
    "score": 4,
    "hook": "phrase d'accroche en moins de 10 mots",
    "verbatim": "citation exacte du passage (50-200 mots maximum)",
    "rationale": "pourquoi ce moment satisfait les critères (1-2 phrases)"
  }}
]

score : 5 = excellent, 4 = bon, 3 = passable, 1-2 = limite.
Si aucun moment ne satisfait les critères, retourne [].
Ne retourne rien d'autre que le JSON."""


def _parse_json_list(text: str) -> list[dict] | None:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    return data


def suggest_shorts(
    vtt_path: Path,
    insights_dir: Path,
    shorts_dir: Path,
    backend: LLMBackend,
    config: Config,
    *,
    force: bool = False,
) -> ShortsResult | None:
    """Suggest Shorts for a single talk. Returns None if transcript is too short."""
    title = _parse_short_title(vtt_path)
    video_id = _extract_video_id(vtt_path)
    json_path = shorts_dir / f"{vtt_path.stem}.json"

    result = ShortsResult(title=title, vtt_path=vtt_path, video_id=video_id, json_path=json_path)

    if json_path.exists() and not force:
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            result.suggestions = [ShortSuggestion.from_dict(d) for d in raw]
            return result
        except (json.JSONDecodeError, KeyError):
            pass

    segments = parse_vtt_timestamped(vtt_path)
    if len(segments) < 20:
        return None

    transcript_text = format_timestamped_transcript(segments, max_chars=config.max_transcript_chars or 18_000)

    insight_md = ""
    insight_md_path = insights_dir / f"{vtt_path.stem}.md"
    if insight_md_path.exists():
        insight_md = insight_md_path.read_text(encoding="utf-8")

    prompt = _build_prompt(title, transcript_text, insight_md)
    text, stop_reason = backend.generate(prompt, max_tokens=config.max_tokens, timeout=config.timeout)

    if stop_reason == "max_tokens":
        warnings.warn(
            f"[{title[:60]}] LLM hit max_tokens — response truncated, skipping cache write.",
            stacklevel=2,
        )
        return None

    raw_list = _parse_json_list(text)

    if raw_list is None:
        simple_prompt = f'{prompt[:1500]}\n\nJSON uniquement: [{{"start":"...","end":"...","score":4,"hook":"...","verbatim":"...","rationale":"..."}}]'
        text2, stop_reason2 = backend.generate(simple_prompt, max_tokens=config.max_tokens, timeout=config.timeout)
        if stop_reason2 != "max_tokens":
            raw_list = _parse_json_list(text2)

    if raw_list is None:
        warnings.warn(f"[{title[:60]}] Could not parse JSON after retry — skipping.", stacklevel=2)
        return None

    suggestions = sorted(
        [ShortSuggestion.from_dict(d) for d in raw_list if isinstance(d, dict)],
        key=lambda s: s.score,
        reverse=True,
    )[:3]
    result.suggestions = suggestions

    shorts_dir.mkdir(parents=True, exist_ok=True)

    tmp_json = json_path.with_suffix(".tmp.json")
    tmp_json.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_json, json_path)

    md_path = shorts_dir / f"{vtt_path.stem}.md"
    tmp_md = md_path.with_suffix(".tmp.md")
    tmp_md.write_text(result.to_markdown(), encoding="utf-8")
    os.replace(tmp_md, md_path)

    return result


def suggest_all(
    vtt_files: list[Path],
    insights_dir: Path,
    shorts_dir: Path,
    backend: LLMBackend,
    config: Config,
    *,
    force: bool = False,
) -> list[ShortsResult]:
    """Process all VTT files concurrently. Returns list of successful results."""
    workers = effective_concurrency(config, backend_type(backend))

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(suggest_shorts, f, insights_dir, shorts_dir, backend, config, force=force): f
            for f in vtt_files
        }
        results: list[ShortsResult] = []
        for fut in as_completed(futures):
            vtt = futures[fut]
            try:
                res = fut.result()
                if res:
                    results.append(res)
            except Exception as exc:
                print(f"  x {vtt.name}: {exc}")

    return results


def generate_index(shorts_dir: Path) -> str:
    """Read all .json files in shorts_dir and generate a markdown index sorted by score."""
    all_shorts: list[dict] = []

    for jf in sorted(shorts_dir.glob("*.json")):
        try:
            suggestions = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        md_path = jf.with_suffix(".md")
        title = jf.stem
        video_id = None
        if md_path.exists():
            md_content = md_path.read_text(encoding="utf-8")
            tm = re.search(r"^# Suggestions Shorts — (.+)$", md_content, re.MULTILINE)
            im = re.search(r"\*\*Video ID :\*\* ([A-Za-z0-9_-]{11})", md_content)
            if tm:
                title = tm.group(1)
            if im:
                video_id = im.group(1)

        for s in suggestions:
            if not isinstance(s, dict):
                continue
            start = s.get("start", "")
            end = s.get("end", "")
            start_secs = 0.0
            duration = 0
            try:
                if start:
                    start_secs = ts_to_seconds(start + ".000" if "." not in start else start)
                if end:
                    end_secs = ts_to_seconds(end + ".000" if "." not in end else end)
                    duration = max(0, int(end_secs - start_secs))
            except (ValueError, AttributeError):
                pass

            link = youtube_link(video_id, start_secs) if video_id and start else ""
            all_shorts.append({
                "title": title,
                "score": int(s.get("score", 0)),
                "start": start,
                "hook": s.get("hook", ""),
                "duration": duration,
                "link": link,
            })

    all_shorts.sort(key=lambda x: x["score"], reverse=True)
    unique_titles = len({s["title"] for s in all_shorts})

    lines = [
        "# Index Shorts — Dev With AI\n",
        f"**{len(all_shorts)} suggestions** issues de {unique_titles} talks, triées par score.\n",
        "---\n",
        "| Score | Hook | Talk | Durée | Lien |",
        "|-------|------|------|-------|------|",
    ]

    for s in all_shorts:
        talk = s["title"][:50] + "..." if len(s["title"]) > 50 else s["title"]
        link_cell = f"[{s['start']}]({s['link']})" if s["link"] else s["start"]
        lines.append(f"| {s['score']}/5 | {s['hook']} | {talk} | {s['duration']}s | {link_cell} |")

    return "\n".join(lines) + "\n"


def generate_short_clip(
    video_id: str,
    start: str,
    end: str,
    output_dir: Path,
    *,
    title: str = "",
    output_format: str = "mp4",
) -> Path | None:
    """Download a video segment using yt-dlp --download-sections.

    No full-video download: yt-dlp fetches only the requested range (~20-50MB).
    Returns the output path on success, None on failure.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^\w\s-]", "", title)[:60].strip() or video_id
    output_template = str(output_dir / f"{safe_title}_{start.replace(':', '')}.%(ext)s")

    cmd = [
        "yt-dlp",
        f"https://www.youtube.com/watch?v={video_id}",
        "--download-sections", f"*{start}-{end}",
        "-o", output_template,
        "--no-playlist",
        "--quiet",
        "--progress",
        "--merge-output-format", output_format,
    ]

    try:
        result = subprocess.run(cmd, check=True, capture_output=False, text=True)
    except subprocess.CalledProcessError as exc:
        warnings.warn(f"yt-dlp failed for {video_id} [{start}-{end}]: {exc}", stacklevel=2)
        return None
    except FileNotFoundError:
        warnings.warn("yt-dlp not found. Install with: pip install yt-dlp", stacklevel=2)
        return None

    candidates = list(output_dir.glob(f"{safe_title}_{start.replace(':', '')}.*"))
    return candidates[0] if candidates else None
