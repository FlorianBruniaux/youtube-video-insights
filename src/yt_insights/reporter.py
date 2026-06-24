"""Aggregate report generation across all video insights."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from .analyzer import VideoInsight
from .backends import LLMBackend
from .config import Config


def load_insights(insights_dir: Path) -> list[VideoInsight]:
    """Load all valid .json insight files from insights_dir."""
    from .analyzer import VideoInsight

    results: list[VideoInsight] = []
    for p in sorted(insights_dir.glob("*.json")):
        if p.stem.startswith("AGGREGATE"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            # Reconstruct a minimal VideoInsight (vtt_path may not exist)
            vi = VideoInsight.from_dict(
                data,
                title=_title_from_stem(p.stem),
                vtt_path=p.with_suffix(".vtt"),
                insight_path=p,
            )
            results.append(vi)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return results


def top_tools(insights: list[VideoInsight], n: int = 20) -> list[tuple[str, int]]:
    """Return top-N tools by mention count across all insights."""
    counter: Counter = Counter()
    for vi in insights:
        for t in vi.tools:
            name = (t.get("name") or "").strip()
            if name:
                counter[name] += 1
    return counter.most_common(n)


def generate_report(
    insights: list[VideoInsight],
    backend: LLMBackend,
    config: Config,
    *,
    report_path: Path,
) -> None:
    """Generate AGGREGATE_REPORT.md and AGGREGATE_REPORT.json.

    Stats (top tools, video count) are computed from code — no LLM.
    A single LLM call synthesises the narrative from key_points + advice.
    """
    tool_counts = top_tools(insights)

    # Build compact JSON payload for the LLM narrative call
    payload = [
        {
            "title": vi.title,
            "subject": vi.subject,
            "key_points": vi.key_points,
            "advice": vi.advice,
        }
        for vi in insights
    ]
    payload_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    prompt = f"""Voici les insights extraits de {len(insights)} vidéos YouTube.

{payload_str}

Génère un rapport synthétique structuré :

**Thèmes récurrents** : quels sujets reviennent le plus ?
**Patterns et conseils récurrents** : ce qui revient dans plusieurs vidéos
**Évolution perceptible** : y a-t-il une progression dans les thèmes traités ?
**Top 5 insights actionnables** : les conseils les plus concrets et utiles

Sois factuel et direct."""

    narrative, _ = backend.generate(
        prompt,
        max_tokens=config.max_tokens,
        timeout=300,  # longer for aggregate
    )

    # Render Markdown
    tools_md = "\n".join(f"- **{name}** ({count})" for name, count in tool_counts)
    md = _render_report(len(insights), tools_md, narrative)

    # Render JSON (raw data, no LLM text)
    json_data = {
        "video_count": len(insights),
        "top_tools": [{"name": n, "count": c} for n, c in tool_counts],
        "videos": [
            {
                "title": vi.title,
                "subject": vi.subject,
                "tools": [t.get("name") for t in vi.tools],
            }
            for vi in insights
        ],
    }

    # Atomic writes
    report_json = report_path.with_suffix(".json")

    tmp_md = report_path.with_suffix(".tmp.md")
    tmp_json = report_json.with_suffix(".tmp.json")

    tmp_md.write_text(md, encoding="utf-8")
    os.replace(tmp_md, report_path)

    tmp_json.write_text(
        json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_json, report_json)


def _render_report(video_count: int, tools_md: str, narrative: str) -> str:
    return f"""# Rapport agrégé : {video_count} vidéos

## Stack et outils cités

{tools_md}

---

{narrative}
"""


def _title_from_stem(stem: str) -> str:
    """Best-effort human title from a VTT stem like '20260101 - Title.fr'."""
    import re
    name = re.sub(r"\.(fr|en)$", "", stem)
    name = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\]$", "", name)
    return name.strip()
