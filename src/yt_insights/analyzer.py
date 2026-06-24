"""Per-video transcript analysis producing structured JSON insights.

Insight schema (requested from LLM and stored):
  {
    "subject":    "...",
    "key_points": ["...", "..."],
    "tools":      [{"name": "...", "context": "..."}],
    "advice":     ["..."],
    "quotes":     ["..."]
  }

Atomicity: insights are written as <name>.tmp.json then os.replace() -> <name>.json.
A Ctrl-C can leave .tmp.json orphans but never a partial .json.

Cache: if <name>.json exists and is valid, it is loaded directly (no LLM call).
Pass force=True to re-analyze regardless of cache.

stop_reason gate: if the LLM reports stop_reason == "max_tokens", the response
was truncated and likely contains invalid JSON. We log a warning and do NOT
write the file — the next run will retry.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .backends import LLMBackend, backend_type
from .cleaner import clean_vtt, parse_title
from .config import Config, effective_concurrency

_INSIGHT_KEYS = {"subject", "key_points", "tools", "advice", "quotes"}


@dataclass
class VideoInsight:
    title: str
    vtt_path: Path
    insight_path: Path
    subject: str = ""
    key_points: list[str] = field(default_factory=list)
    tools: list[dict] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)
    quotes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict, *, title: str, vtt_path: Path, insight_path: Path) -> "VideoInsight":
        return cls(
            title=title,
            vtt_path=vtt_path,
            insight_path=insight_path,
            subject=data.get("subject", ""),
            key_points=data.get("key_points", []),
            tools=data.get("tools", []),
            advice=data.get("advice", []),
            quotes=data.get("quotes", []),
        )

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "key_points": self.key_points,
            "tools": self.tools,
            "advice": self.advice,
            "quotes": self.quotes,
        }

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        if self.subject:
            lines += [f"**Sujet principal** : {self.subject}", ""]
        if self.key_points:
            lines += ["**Points clés** :"]
            lines += [f"- {p}" for p in self.key_points]
            lines.append("")
        if self.tools:
            lines += ["**Outils / technos** :"]
            for t in self.tools:
                name = t.get("name", "")
                ctx = t.get("context", "")
                lines.append(f"- **{name}**" + (f" : {ctx}" if ctx else ""))
            lines.append("")
        if self.advice:
            lines += ["**Conseils actionnables** :"]
            lines += [f"- {a}" for a in self.advice]
            lines.append("")
        if self.quotes:
            lines += ["**Citations notables** :"]
            lines += [f"> {q}" for q in self.quotes]
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def analyze_video(
    vtt_path: Path,
    insights_dir: Path,
    backend: LLMBackend,
    config: Config,
    *,
    force: bool = False,
) -> VideoInsight | None:
    title = parse_title(vtt_path)
    insight_path = insights_dir / f"{vtt_path.stem}.json"

    # Cache hit
    if insight_path.exists() and not force:
        try:
            data = json.loads(insight_path.read_text(encoding="utf-8"))
            return VideoInsight.from_dict(data, title=title, vtt_path=vtt_path, insight_path=insight_path)
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupt cache — re-analyze

    # Clean transcript
    transcript = clean_vtt(vtt_path)
    if len(transcript) < 200:
        return None

    # Build prompt
    excerpt = transcript[: config.max_transcript_chars]
    if len(transcript) > config.max_transcript_chars:
        excerpt += "\n[... transcript truncated ...]"

    prompt = _build_prompt(title, excerpt)

    # First attempt
    text, stop_reason = backend.generate(
        prompt, max_tokens=config.max_tokens, timeout=config.timeout
    )

    if stop_reason == "max_tokens":
        warnings.warn(
            f"[{title[:60]}] LLM hit max_tokens — response truncated, skipping cache write.",
            stacklevel=2,
        )
        return None

    data = _parse_json(text)

    # One retry with simplified prompt if parse failed
    if data is None:
        simple_prompt = _build_prompt_simple(title, excerpt)
        text2, stop_reason2 = backend.generate(
            simple_prompt, max_tokens=config.max_tokens, timeout=config.timeout
        )
        if stop_reason2 != "max_tokens":
            data = _parse_json(text2)

    if data is None:
        warnings.warn(
            f"[{title[:60]}] Could not parse JSON after retry — skipping.",
            stacklevel=2,
        )
        return None

    insight = VideoInsight.from_dict(data, title=title, vtt_path=vtt_path, insight_path=insight_path)

    # Atomic write: .tmp.json -> .json
    insights_dir.mkdir(parents=True, exist_ok=True)
    tmp_json = insight_path.with_suffix(".tmp.json")
    tmp_json.write_text(json.dumps(insight.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_json, insight_path)

    # Atomic write: .tmp.md -> .md
    md_path = insights_dir / f"{vtt_path.stem}.md"
    tmp_md = md_path.with_suffix(".tmp.md")
    tmp_md.write_text(insight.to_markdown(), encoding="utf-8")
    os.replace(tmp_md, md_path)

    return insight


def analyze_all(
    vtt_files: list[Path],
    insights_dir: Path,
    backend: LLMBackend,
    config: Config,
    *,
    force: bool = False,
) -> list[VideoInsight]:
    workers = effective_concurrency(config, backend_type(backend))

    # httpx.Client is thread-safe for concurrent requests
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(analyze_video, f, insights_dir, backend, config, force=force): f
            for f in vtt_files
        }
        results: list[VideoInsight] = []
        for fut in as_completed(futures):
            vtt = futures[fut]
            try:
                insight = fut.result()
                if insight:
                    results.append(insight)
            except Exception as exc:
                print(f"  x {vtt.name}: {exc}")

    return results


# ------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------

def _build_prompt(title: str, transcript: str) -> str:
    return f"""Voici la transcription d'une vidéo YouTube : "{title}"

---
{transcript}
---

Réponds uniquement avec un objet JSON valide, sans markdown, sans texte autour.
Schéma exact (respecte les noms de clés) :

{{
  "subject": "sujet principal en une phrase",
  "key_points": ["point 1", "point 2", "point 3"],
  "tools": [{{"name": "outil", "context": "usage dans la vidéo"}}],
  "advice": ["conseil actionnable 1", "conseil actionnable 2"],
  "quotes": ["citation notable si pertinent"]
}}

Si une section est vide, retourne un tableau vide [].
Ne retourne rien d'autre que le JSON."""


def _build_prompt_simple(title: str, transcript: str) -> str:
    return f"""Transcription : "{title}"

{transcript[:5000]}

JSON uniquement, schéma :
{{"subject":"...","key_points":[],"tools":[],"advice":[],"quotes":[]}}"""


def _parse_json(text: str) -> dict | None:
    # Strip markdown fences if present (small models often add them)
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract first JSON object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None

    # Validate required keys
    if not isinstance(data, dict):
        return None
    if not _INSIGHT_KEYS.issubset(data.keys()):
        return None

    return data
