#!/usr/bin/env python3
"""Regenerate the yt-insights output catalog from existing insight JSONs.

Produces three layers, all deterministic (no LLM), safe to re-run after adding
videos or a whole channel:

  1. output/<slug>/INDEX.md   per-channel index, one detailed recap per video
  2. output/CATALOG.yaml      machine feed: stats, per-channel blocks, and an
                              inverted topic -> channels index
  3. output/INDEX.md          global human index above the per-channel ones

Usage:  python3 scripts/build_index.py
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
# Anchored: report/index files start with these tokens; video files start with a
# date (YYYYMMDD). A substring match would wrongly skip videos titled "...Report",
# "Full Interview", etc.
SKIP = re.compile(r"^(AGGREGATE_REPORT|FULL_REPORT|INDEX)\.", re.IGNORECASE)
NAME_RE = re.compile(r"^(?P<date>\d{8})\s*-\s*(?P<title>.*?)\s*\[(?P<id>[A-Za-z0-9_-]{11})\]")

# Curated one-line descriptions (from the hand-written llms.txt). Fallback = slug.
DESCRIPTIONS = {
    "stanford": "Stanford Online — académique : ML, LLM, scaling, GPU, robotique",
    "devoxx": "Devoxx — conférences dev : Java/JVM, DevOps, archi, IA",
    "tpc": "The Product Crew — produit / product management (FR)",
    "ifttd": "If This Then Dev — podcast dev généraliste (FR)",
    "bytebytego": "ByteByteGo — system design, scalabilité",
    "devwithai": "Dev With AI — développement IA-natif (FR)",
    "techready": "Tech Ready — IA en entreprise (corpus promotionnel)",
    "co-cto": "co-CTO — rôle et transition CTO (FR)",
    "martignole": "Nicolas Martignole — journal de bord Claude Code en prod (FR)",
    "aidevcon": "AI Dev Con — conférences IA appliquée au développement",
    "alafrench": "À La French — tech, IA, souveraineté numérique française (FR)",
    "methode-aristote": "Méthode Aristote — éducation / plateforme interne",
    "bloomberg": "Bloomberg — tech/marchés : IA, semi-conducteurs, big tech (EN)",
}

# Tool-name normalization for the topic index (merge obvious variants).
TOPIC_ALIASES = {
    "llms": "llm", "large language model": "llm", "large language models": "llm",
    "gpt-4": "gpt", "gpt-5": "gpt", "chatgpt": "gpt", "chatgpt for finance": "gpt",
    # MCP variants (small model emits several spellings)
    "model context protocol": "mcp", "model context protocol (mcp)": "mcp",
    "mcp (model context protocol)": "mcp", "mcp servers": "mcp", "mcp server": "mcp",
    # Claude Code, incl. the frequent "Cloud Code" mistranscription
    "cloud code": "claude code", "claude-code": "claude code",
    # other common variants
    "tessle": "tessl", "tessell": "tessl",
    "github copilot": "copilot", "gh copilot": "copilot",
    "k8s": "kubernetes",
    "postgresql": "postgres", "postgres sql": "postgres",
    "js": "javascript", "ts": "typescript",
    "olama": "ollama",
}
MIN_TOPIC_COUNT = 3  # a topic must appear >= this many times globally to be listed


def parse_name(stem: str):
    base = re.sub(r"\.json$", "", stem)
    m = NAME_RE.match(base)
    if m:
        d = m.group("date")
        return d, f"{d[0:4]}-{d[4:6]}-{d[6:8]}", m.group("title").strip(), m.group("id")
    mid = re.search(r"\[([A-Za-z0-9_-]{11})\]", base)
    vid = mid.group(1) if mid else ""
    title = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\].*$", "", base).strip()
    return "00000000", "", title or base, vid


def load_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def tool_names(data: dict):
    out = []
    for t in data.get("tools") or []:
        name = as_text(t.get("name")) if isinstance(t, dict) else as_text(t)
        if name:
            out.append(name)
    return out


def norm_topic(name: str) -> str:
    key = name.strip().lower()
    return TOPIC_ALIASES.get(key, key)


def as_text(v) -> str:
    """Coerce any insight value (str/dict/list) to a readable one-liner.

    Small local models occasionally emit a dict where a string was expected
    (e.g. subject={'name': 'Apple', 'role': 'Tech Company'}). Flatten instead
    of crashing.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        # prefer a title-ish key, else join values
        for k in ("title", "name", "subject", "summary", "text"):
            if isinstance(v.get(k), str) and v[k].strip():
                return v[k].strip()
        return ", ".join(as_text(x) for x in v.values() if as_text(x))
    if isinstance(v, list):
        return "; ".join(as_text(x) for x in v if as_text(x))
    return str(v).strip()


# ---------------------------------------------------------------- per-channel MD
def render_video_md(json_path: Path) -> tuple[str, str]:
    date_key, date_iso, title, vid = parse_name(json_path.name)
    data = load_json(json_path)
    if data is None:
        return date_key, f"## {title}\n\n_(insight illisible)_\n\n---\n"

    abs_md = str(json_path.with_suffix(".md").resolve())
    lines = [f"## {title}", ""]
    meta = []
    if date_iso:
        meta.append(f"**Date** : {date_iso}")
    if vid:
        meta.append(f"**Vidéo** : https://www.youtube.com/watch?v={vid}")
    if meta:
        lines.append("  \n".join(meta) + "\n")

    if (subject := as_text(data.get("subject"))):
        lines.append(f"**Sujet** : {subject}\n")
    if (kp := [t for p in (data.get("key_points") or []) if (t := as_text(p))]):
        lines.append("**Points clés** :")
        lines += [f"- {p}" for p in kp]
        lines.append("")
    tools = []
    for t in data.get("tools") or []:
        if isinstance(t, dict):
            n, c = as_text(t.get("name")), as_text(t.get("context"))
            if n:
                tools.append(f"- **{n}**" + (f" : {c}" if c else ""))
        elif as_text(t):
            tools.append(f"- **{as_text(t)}**")
    if tools:
        lines.append("**Outils / technos** :")
        lines += tools
        lines.append("")
    if (advice := [t for a in (data.get("advice") or []) if (t := as_text(a))]):
        lines.append("**Conseils actionnables** :")
        lines += [f"- {a}" for a in advice]
        lines.append("")
    if (quotes := [t for q in (data.get("quotes") or []) if (t := as_text(q))]):
        lines.append("**Citations notables** :")
        lines += [f"> {q}" for q in quotes]
        lines.append("")
    lines.append(f"**Fichier** : `{abs_md}`\n")
    lines.append("---\n")
    return date_key, "\n".join(lines)


def scan_channel(insights_dir: Path):
    """Return (rendered_videos, date_min, date_max, tool_counter, video_count)."""
    jsons = [p for p in insights_dir.glob("*.json") if not SKIP.search(p.name)]
    rendered, dates, tools = [], [], Counter()
    for p in jsons:
        date_key, md = render_video_md(p)
        rendered.append((date_key, md))
        if date_key != "00000000":
            dates.append(date_key)
        data = load_json(p)
        if data:
            for n in tool_names(data):
                tools[n] += 1
    rendered.sort(key=lambda x: x[0], reverse=True)
    dmin = min(dates) if dates else None
    dmax = max(dates) if dates else None
    return rendered, dmin, dmax, tools, len(jsons)


def iso(d: str | None) -> str | None:
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}" if d else None


def write_channel_index(slug: str, rendered, count: int) -> Path:
    header = [
        f"# {slug} — Index des vidéos",
        "",
        f"{count} vidéos analysées. Récap par vidéo (sujet, points clés, outils, "
        "conseils, citations) et chemin absolu vers le fichier insight.",
        "",
        "_Généré depuis les JSON d'insights. Trié par date décroissante._",
        "",
        "---",
        "",
    ]
    path = OUTPUT / slug / "INDEX.md"
    path.write_text("\n".join(header) + "\n".join(r for _, r in rendered), encoding="utf-8")
    return path


# ------------------------------------------------------------------- main build
def main():
    today = date.today().isoformat()
    channels = {}
    topic_index: dict[str, Counter] = defaultdict(Counter)  # topic -> {slug: count}

    for d in sorted(OUTPUT.iterdir()):
        if not d.is_dir():
            continue
        slug = d.name
        insights_dir = d / "insights"
        synthesis = next(iter(sorted(d.glob("*synthesis*.md"))), None)
        has_insights = insights_dir.is_dir() and any(
            p for p in insights_dir.glob("*.json") if not SKIP.search(p.name)
        )
        if not has_insights and synthesis is None:
            continue

        entry = {
            "slug": slug,
            "description": DESCRIPTIONS.get(slug, slug),
            "video_count": 0,
            "date_range": None,
            "paths": {},
            "top_topics": [],
        }
        if synthesis:
            entry["paths"]["synthesis"] = str(synthesis.resolve())

        if has_insights:
            rendered, dmin, dmax, tools, count = scan_channel(insights_dir)
            entry["video_count"] = count
            if dmin and dmax:
                entry["date_range"] = [iso(dmin), iso(dmax)]
            idx = write_channel_index(slug, rendered, count)
            entry["paths"]["index_md"] = str(idx.resolve())
            entry["paths"]["insights_dir"] = str(insights_dir.resolve())
            agg = insights_dir / "AGGREGATE_REPORT.md"
            if agg.exists():
                entry["paths"]["aggregate_report"] = str(agg.resolve())
            # top topics for this channel + feed the inverted index
            for name, c in tools.most_common():
                topic_index[norm_topic(name)][slug] += c
            entry["top_topics"] = [n for n, _ in tools.most_common(15)]
        channels[slug] = entry

    # Global inverted topic index, keep only recurrent topics.
    topics_global = {
        t: dict(cnt.most_common())
        for t, cnt in topic_index.items()
        if sum(cnt.values()) >= MIN_TOPIC_COUNT
    }
    topics_sorted = dict(
        sorted(topics_global.items(), key=lambda kv: sum(kv[1].values()), reverse=True)
    )

    total_videos = sum(c["video_count"] for c in channels.values())

    # ---- write CATALOG.yaml (hand-rolled, no external yaml dep) -------------
    def q(s: str) -> str:
        return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

    y = []
    y.append("# yt-insights — Catalogue machine-lisible (feed pour session LLM)")
    y.append(f"# Généré le {today}. Régénérer : python3 scripts/build_index.py")
    y.append("# But : savoir vite si le corpus couvre un sujet, et où.")
    y.append("stats:")
    y.append(f"  generated: {q(today)}")
    y.append(f"  channels: {len([c for c in channels.values() if c['video_count']])}")
    y.append(f"  total_videos: {total_videos}")
    y.append(f"  distinct_topics: {len(topics_sorted)}")
    y.append(f"  global_index_md: {q(str((OUTPUT / 'INDEX.md').resolve()))}")
    y.append(f"  narrative_llms_txt: {q(str((OUTPUT / 'llms.txt').resolve()))}")
    y.append("")
    y.append("channels:")
    for slug, e in channels.items():
        y.append(f"  {slug}:")
        y.append(f"    description: {q(e['description'])}")
        y.append(f"    video_count: {e['video_count']}")
        if e["date_range"]:
            y.append(f"    date_range: [{q(e['date_range'][0])}, {q(e['date_range'][1])}]")
        y.append("    paths:")
        for k, v in e["paths"].items():
            y.append(f"      {k}: {q(v)}")
        if e["top_topics"]:
            y.append("    top_topics: [" + ", ".join(q(t) for t in e["top_topics"]) + "]")
    y.append("")
    y.append("# Index inversé : topic -> {chaîne: occurrences}. Trié par fréquence globale.")
    y.append("topics_index:")
    for t, chans in topics_sorted.items():
        inline = ", ".join(f"{s}: {c}" for s, c in chans.items())
        y.append(f"  {q(t)}: {{{inline}}}")
    (OUTPUT / "CATALOG.yaml").write_text("\n".join(y) + "\n", encoding="utf-8")

    # ---- write global INDEX.md --------------------------------------------
    m = []
    m.append("# yt-insights — Index global")
    m.append("")
    m.append(
        f"{len([c for c in channels.values() if c['video_count']])} chaînes, "
        f"{total_videos} vidéos analysées. Point d'entrée au-dessus des index par chaîne. "
        f"Pour un feed machine, voir `CATALOG.yaml`."
    )
    m.append("")
    m.append(f"_Généré le {today} par `scripts/build_index.py`._")
    m.append("")
    m.append("## Chaînes")
    m.append("")
    m.append("| Chaîne | Vidéos | Période | Index | Synthèse |")
    m.append("|---|---|---|---|---|")
    for slug, e in sorted(channels.items(), key=lambda kv: -kv[1]["video_count"]):
        dr = f"{e['date_range'][0]} → {e['date_range'][1]}" if e["date_range"] else "—"
        idx = f"`{e['paths'].get('index_md', '—')}`" if "index_md" in e["paths"] else "—"
        syn = "oui" if "synthesis" in e["paths"] else "—"
        m.append(f"| {slug} | {e['video_count']} | {dr} | {idx} | {syn} |")
    m.append("")
    m.append("## Sujets couverts (index inversé)")
    m.append("")
    m.append("Chaque sujet liste les chaînes qui en parlent (occurrences décroissantes). "
             "Sert à répondre vite : « a-t-on de la data là-dessus ? »")
    m.append("")
    for t, chans in list(topics_sorted.items())[:60]:
        chan_str = ", ".join(f"{s} ({c})" for s, c in chans.items())
        m.append(f"- **{t}** — {chan_str}")
    m.append("")
    (OUTPUT / "INDEX.md").write_text("\n".join(m) + "\n", encoding="utf-8")

    print(f"Chaînes: {len(channels)} | Vidéos: {total_videos} | Topics: {len(topics_sorted)}")
    print(f"Écrit : {OUTPUT/'CATALOG.yaml'}")
    print(f"Écrit : {OUTPUT/'INDEX.md'}")
    print("Per-channel INDEX.md régénérés.")


if __name__ == "__main__":
    main()
