#!/usr/bin/env python3
"""Regenerate output/speakers.md from existing insight video titles.

Deterministic regex pass over the same filenames build_index.py already
parses (no LLM call, safe to re-run after adding videos or a whole channel).
Extracts a speaker name from the video title when the title follows one of a
few known conventions:

  - "... by Speaker Name"                  (devoxx, techready EN, martignole)
  - "... par Speaker Name[ et Second Name]" (techready FR, martignole FR)
  - "... with Speaker Name[ from Company]"  (aidevcon)
  - "... - Speaker Name @ Company"          (tpc)
  - "Speaker Name - Title"                  (devwithai only, name-first)
  - "... - with @handle"                    (pragmaticengineer only)

Coverage is reliable on channels whose video titles name the speaker
(devoxx, aidevcon, techready, devwithai, pragmaticengineer, martignole, tpc).
It is near-zero on single-host channels without a named guest in the title
(bloomberg, co-cto, alafrench, indydevdan, methode-aristote,
optimisemonespace, ifttd) --
that is a title-format gap, not proof those channels lack guests. Reading
transcript content to find guests there would need a separate, non-regex
pass (LLM extraction), out of scope for this script.

Usage:  python3 scripts/build_speakers.py
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
SKIP = re.compile(r"^(AGGREGATE_REPORT|FULL_REPORT|INDEX)\.", re.IGNORECASE)

NAME = r"[A-ZÀ-Ý][\wÀ-ÿ.'\u2019\u2013-]+"
FULLNAME = rf"{NAME}(?: {NAME}){{0,3}}"

# Words the regexes above occasionally grab that are not people (a leftover
# topic/tool token at the end of a title, or a truncated match). Extend this
# set as new false positives show up after adding a channel.
NOISE = {
    "agents", "authentication", "claude code", "codex", "cursor", "gemini cli",
    "generative ai", "hub", "oli", "guill", "christopher", "sourcegraph", "tessl",
    "des", "ai", "cnns", "computer vision", "confidence", "examples",
    "exceptional reach", "foundation models", "linear classifiers",
    "logistic regression", "techniques borrowed", "project leyden", "oxide",
    "data center power demand", "electric motors", "regulation",
    "ai products", "founding engineer", "interview founder", "product ops",
    "vp data", "collaborative ai agents", "cpo", "cpto", "cto", "camille",
    "data", "design", "engineering", "entretien technique développeur",
    "flavien", "floriane", "hypercroissance", "lucie", "maja", "maurine",
    "maxime", "pierre", "product", "romain", "samy", "tech", "thomas",
    "aider architect", "git worktrees", "sota accuracy",
}


def clean_name(n: str) -> str:
    n = n.strip()
    n = re.sub(r"\s*\[.*$", "", n)
    n = re.sub(r"\s*[\(\uff08].*$", "", n)
    n = n.strip(" -\u2013\u2014\uff1a:")
    n = re.sub(r"^(CEO|Dr\.?|Prof\.?)\s+", "", n)
    n = re.sub(r".*'s\s+", "", n)  # "Traceloop's Gal Kleinman" -> "Gal Kleinman"
    return n.strip()


def extract_names(title: str, slug: str) -> list[str]:
    names: list[str] = []

    m = re.search(rf"\bby ({FULLNAME})(?: and ({FULLNAME}))?\s*$", title)
    if m:
        names = [g for g in m.groups() if g]

    if not names:
        m = re.search(rf"\bpar ({FULLNAME})(?: et ({FULLNAME}))?\s*$", title)
        if m:
            names = [g for g in m.groups() if g]

    if not names:
        m = re.search(rf"\bwith (@?{FULLNAME})(?:\s+from\s+.*)?\s*$", title)
        if m:
            cand = m.group(1)
            if not cand.lower().startswith(("the ", "a ", "deep", "learn", "llm", "ai ", "gpt")):
                names = [cand]

    if not names:
        m = re.search(rf" - ({FULLNAME}) @ ", title)
        if m:
            names = [m.group(1)]

    if not names and slug == "devwithai":
        m = re.match(rf"^\d{{8}} - ({FULLNAME}) - ", title)
        if m:
            names = [m.group(1)]

    if not names and slug == "pragmaticengineer":
        m = re.search(r" - with @?([A-Za-z0-9_]+)\s*$", title)
        if m:
            names = [m.group(1)]

    return names


def scan_channel(insights_dir: Path, slug: str) -> set[str]:
    found: set[str] = set()
    for p in insights_dir.glob("*.json"):
        if SKIP.search(p.name):
            continue
        stem = re.sub(r"\.json$", "", p.name)
        title = re.sub(r"\s*\[[A-Za-z0-9_-]{11}\].*$", "", stem).strip()
        for raw in extract_names(title, slug):
            name = clean_name(raw)
            if name.lower() in NOISE or not (2 < len(name) < 60):
                continue
            found.add(name)
    return found


def main() -> None:
    today = date.today().isoformat()
    by_channel: dict[str, set[str]] = defaultdict(set)

    for d in sorted(OUTPUT.iterdir()):
        if not d.is_dir():
            continue
        insights_dir = d / "insights"
        if not insights_dir.is_dir():
            continue
        names = scan_channel(insights_dir, d.name)
        if names:
            by_channel[d.name] = names

    lines = [
        "# yt-insights — speakers extraits",
        "",
        f"_Généré le {today} par `scripts/build_speakers.py`. Régénérer après tout "
        "ajout de chaîne ou de vidéo : `python3 scripts/build_speakers.py`. Appelé "
        "automatiquement en fin de `runbook/run-channel.sh`, pas besoin de le lancer "
        "à la main après un `/yt-add-channel`._",
        "",
        "Extraction déterministe par regex sur les titres de vidéo (voir le docstring "
        "du script pour les patterns reconnus : `by X`, `par X`, `with X`, `X @ "
        "Company`, etc.). Fiable sur les chaînes de conférence et d'interview "
        "(devoxx, aidevcon, techready, devwithai, pragmaticengineer, martignole, "
        "tpc). Quasi vide sur les chaînes mono-hôte sans invité nommé dans le titre "
        "(bloomberg, co-cto, alafrench, indydevdan, methode-aristote, "
        "optimisemonespace, ifttd) : "
        "c'est un trou de format de titre, pas une absence d'invités dans ces "
        "vidéos.",
        "",
    ]

    total = sum(len(v) for v in by_channel.values())
    lines.append(f"**{total} entrées** (une personne apparaissant sur plusieurs chaînes compte "
                 "une fois par chaîne, pas de dédup inter-chaîne).")
    lines.append("")

    for slug in sorted(by_channel, key=lambda s: -len(by_channel[s])):
        names = by_channel[slug]
        lines.append(f"## {slug} ({len(names)})")
        lines.append("")
        for n in sorted(names):
            lines.append(f"- {n}")
        lines.append("")

    (OUTPUT / "speakers.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Chaînes avec speakers détectés : {len(by_channel)} | Total entrées : {total}")
    print(f"Écrit : {OUTPUT / 'speakers.md'}")


if __name__ == "__main__":
    main()
