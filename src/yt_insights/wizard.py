"""Interactive wizard for yt-insights.

Two modes:
- TTY detected  → InquirerPy prompts (arrow-key selection)
- No TTY        → print structured questions, expect flags --action/--source/etc.
                  Designed for Claude Code: Claude reads the output, asks the user,
                  then re-runs with all flags provided.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .config import load_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLATFORMS: dict[str, dict] = {
    "youtube-shorts": {"label": "YouTube Shorts", "max": 60},
    "tiktok":         {"label": "TikTok",          "max": 60},
    "reels":          {"label": "Instagram Reels", "max": 90},
    "linkedin":       {"label": "LinkedIn",         "max": 90},
    "none":           {"label": "Aucune contrainte","max": 999},
}

DURATION_RANGES: dict[str, tuple[int, int]] = {
    "very-short": (15,  30),
    "standard":   (30,  60),
    "long":       (60,  90),
    "any":        (0,  999),
}

DURATION_LABELS: dict[str, str] = {
    "very-short": "Très court (15-30s)",
    "standard":   "Standard (30-60s)",
    "long":       "Long (60-90s)",
    "any":        "Pas de contrainte (0-90s)",
}

OUTPUT_FORMATS = ["mp4", "webm", "mkv"]

ACTIONS: dict[str, str] = {
    "insights": "Analyser une vidéo (extraire les insights)",
    "shorts":   "Suggérer des moments Shorts depuis les VTT en cache",
    "clip":     "Télécharger un clip Short depuis les suggestions en cache",
    "pipeline": "Pipeline complet (download + insights + shorts + clip)",
}

# ---------------------------------------------------------------------------
# TTY detection
# ---------------------------------------------------------------------------

def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Non-interactive mode: print questions and exit
# ---------------------------------------------------------------------------

MISSING_PARAMS_NOTICE = """
yt-insights interactive — mode non-interactif détecté (pas de TTY).

Réponds aux questions ci-dessous, puis relance avec les flags correspondants :

  yt-insights interactive \\
    --action   <action>   \\
    --source   <url>      \\   (si action = insights ou pipeline)
    --duration <durée>    \\   (si action implique des Shorts)
    --platform <platform> \\   (si action implique des Shorts)
    --format   <format>       (si action implique un clip)

"""

def _print_non_interactive_guide(missing: list[str]) -> None:
    print(MISSING_PARAMS_NOTICE)

    if "action" in missing:
        print("ACTION — Que veux-tu faire ?")
        for key, label in ACTIONS.items():
            print(f"  --action {key:<12}  {label}")
        print()

    if "source" in missing:
        print("SOURCE — URL YouTube (vidéo, playlist ou chaîne) :")
        print("  --source 'https://www.youtube.com/watch?v=...'")
        print()

    if "duration" in missing:
        print("DURATION — Durée préférée pour le Short ?")
        for key, label in DURATION_LABELS.items():
            print(f"  --duration {key:<12}  {label}")
        print()

    if "platform" in missing:
        print("PLATFORM — Plateforme cible ?")
        for key, meta in PLATFORMS.items():
            cap = f"max {meta['max']}s" if meta["max"] < 999 else "aucune limite"
            print(f"  --platform {key:<16}  {meta['label']} ({cap})")
        print()

    if "format" in missing:
        print("FORMAT — Format de sortie du clip ?")
        print("  --format mp4    MP4  (recommandé — compatible partout)")
        print("  --format webm   WebM (natif YouTube, léger)")
        print("  --format mkv    MKV  (conteneur universel)")
        print()


# ---------------------------------------------------------------------------
# InquirerPy prompts (TTY only)
# ---------------------------------------------------------------------------

def _prompt_action() -> str:
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    return inquirer.select(
        message="Que veux-tu faire ?",
        choices=[Choice(value=k, name=v) for k, v in ACTIONS.items()],
    ).execute()


def _prompt_source() -> str:
    from InquirerPy import inquirer
    return inquirer.text(
        message="URL YouTube (vidéo, playlist ou chaîne) :",
        validate=lambda v: len(v.strip()) > 0,
        invalid_message="L'URL ne peut pas être vide.",
    ).execute().strip()


def _prompt_duration() -> str:
    from InquirerPy import inquirer
    return inquirer.select(
        message="Durée préférée pour le Short ?",
        choices=[{"name": label, "value": key} for key, label in DURATION_LABELS.items()],
        default="standard",
    ).execute()


def _prompt_platform() -> str:
    from InquirerPy import inquirer
    choices = [
        {"name": f"{meta['label']} (max {meta['max']}s)" if meta["max"] < 999 else meta["label"], "value": key}
        for key, meta in PLATFORMS.items()
    ]
    return inquirer.select(
        message="Plateforme cible ?",
        choices=choices,
        default="youtube-shorts",
    ).execute()


def _prompt_format() -> str:
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    return inquirer.select(
        message="Format de sortie du clip ?",
        choices=[
            Choice(value="mp4",  name="MP4  (recommandé — compatible partout)"),
            Choice(value="webm", name="WebM (natif YouTube, léger)"),
            Choice(value="mkv",  name="MKV  (conteneur universel)"),
        ],
        default="mp4",
    ).execute()


def _pick_suggestion_tty(pairs: list[tuple]) -> tuple:
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    choices = [
        Choice(
            value=i,
            name=f"[{s.score}/5] {s.start}→{s.end} ({s.duration}s) — {s.hook}",
        )
        for i, (vid_id, s) in enumerate(pairs)
    ]
    idx = inquirer.select(message="Quel clip veux-tu télécharger ?", choices=choices).execute()
    return pairs[idx]


# ---------------------------------------------------------------------------
# Core actions (shared between TTY and non-TTY)
# ---------------------------------------------------------------------------

def _needs_source(action: str) -> bool:
    return action in ("insights", "pipeline")

def _needs_shorts_params(action: str) -> bool:
    return action in ("shorts", "clip", "pipeline")


def _run_insights(source: str, config):
    from .analyzer import analyze_all
    from .backends import resolve_backend
    from .backends.base import BackendNotFoundError, BackendUnavailableError
    from .downloader import download_subtitles

    print(f"\nTéléchargement des sous-titres depuis {source} ...")
    result = download_subtitles(source, config.transcripts_dir)
    for e in result.errors:
        print(f"  avertissement: {e}", file=sys.stderr)

    vtt_files = result.vtt_files or sorted(config.transcripts_dir.glob("*.vtt"))
    if not vtt_files:
        print("Aucun fichier VTT trouvé.", file=sys.stderr)
        return []

    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        print(f"Erreur backend : {exc}", file=sys.stderr)
        return []

    print(f"Analyse de {len(vtt_files)} vidéo(s) avec '{config.model}' ...")
    try:
        insights = analyze_all(vtt_files, config.insights_dir, backend, config)
    except BackendUnavailableError as exc:
        print(f"Erreur backend : {exc}", file=sys.stderr)
        return []
    finally:
        backend.close()

    print(f"{len(insights)} insight(s) générés.")
    return vtt_files


def _run_suggest_shorts(vtt_files: list[Path], config, min_s: int, max_s: int, platform_max: int):
    from .backends import resolve_backend
    from .backends.base import BackendNotFoundError, BackendUnavailableError
    from .shorts import generate_index, suggest_all

    if not vtt_files:
        vtt_files = sorted(config.transcripts_dir.glob("*.vtt"))
    if not vtt_files:
        print("Aucun VTT trouvé. Lance d'abord l'analyse.", file=sys.stderr)
        return []

    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        print(f"Erreur backend : {exc}", file=sys.stderr)
        return []

    config.shorts_dir.mkdir(parents=True, exist_ok=True)
    print(f"Recherche de Shorts dans {len(vtt_files)} vidéo(s) ...")
    try:
        results = suggest_all(vtt_files, config.insights_dir, config.shorts_dir, backend, config)
    except BackendUnavailableError as exc:
        print(f"Erreur backend : {exc}", file=sys.stderr)
        return []
    finally:
        backend.close()

    (config.shorts_dir / "INDEX.md").write_text(generate_index(config.shorts_dir), encoding="utf-8")

    effective_max = min(max_s, platform_max)
    pairs = [
        (r.video_id, s)
        for r in results
        for s in r.suggestions
        if min_s <= s.duration <= effective_max
    ]

    if not pairs:
        print("Aucune suggestion ne correspond aux critères de durée/plateforme.", file=sys.stderr)
    else:
        print(f"{len(pairs)} suggestion(s) après filtrage :")
        for vid_id, s in pairs:
            print(f"  [{s.score}/5] {s.start}→{s.end} ({s.duration}s) — {s.hook}")

    return pairs


def _load_cached_suggestions(config, min_s: int, max_s: int, platform_max: int):
    from .shorts import ShortSuggestion

    effective_max = min(max_s, platform_max)
    pairs = []
    for json_path in sorted(config.shorts_dir.glob("*.json")):
        try:
            raw = json.loads(json_path.read_text())
            m = re.search(r"\[([A-Za-z0-9_-]{11})\]", json_path.stem)
            vid_id = m.group(1) if m else None
            for d in raw:
                s = ShortSuggestion.from_dict(d)
                if min_s <= s.duration <= effective_max:
                    pairs.append((vid_id, s))
        except Exception:
            continue
    return pairs


def _download_clip(vid_id: str, suggestion, output_format: str, config):
    from .shorts import generate_short_clip

    safe_hook = re.sub(r"[^\w\s-]", "", suggestion.hook)[:40].strip().replace(" ", "-") or "short"
    print(f"\nTéléchargement [{suggestion.start}→{suggestion.end}] en {output_format.upper()} ...")
    clip_path = generate_short_clip(
        vid_id, suggestion.start, suggestion.end,
        config.shorts_clips_dir, title=safe_hook, output_format=output_format,
    )
    if clip_path:
        size_mb = clip_path.stat().st_size / (1024 * 1024)
        print(f"  Sauvegardé : {clip_path} ({size_mb:.1f} MB)")
    else:
        print("  Échec du téléchargement.", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_wizard(
    action: str | None = None,
    source: str | None = None,
    duration: str | None = None,
    platform: str | None = None,
    output_format: str | None = None,
) -> None:
    config = load_config({})
    tty = _is_tty()

    # --- Collect missing params ---
    missing = []
    if action is None:
        missing.append("action")
    if action in (None, "insights", "pipeline") and source is None:
        missing.append("source")
    if action in (None, "shorts", "clip", "pipeline") and duration is None:
        missing.append("duration")
    if action in (None, "shorts", "clip", "pipeline") and platform is None:
        missing.append("platform")
    if action in (None, "shorts", "clip", "pipeline") and output_format is None:
        missing.append("format")

    # Non-TTY with missing params: print guide and exit
    if not tty and missing:
        _print_non_interactive_guide(missing)
        sys.exit(0)

    # TTY with missing params: prompt for them
    if tty:
        if action is None:
            action = _prompt_action()
        if _needs_source(action) and source is None:
            source = _prompt_source()
        if _needs_shorts_params(action):
            if duration is None:
                duration = _prompt_duration()
            if platform is None:
                platform = _prompt_platform()
            if output_format is None:
                output_format = _prompt_format()
        if not _needs_shorts_params(action):
            duration = duration or "any"
            platform = platform or "none"
            output_format = output_format or "mp4"

    # Resolve duration + platform to numbers
    min_s, max_s = DURATION_RANGES.get(duration or "any", (0, 999))
    platform_max = PLATFORMS.get(platform or "none", {}).get("max", 999)

    print()

    # --- Execute ---
    vtt_files: list[Path] = []

    if action == "insights":
        _run_insights(source, config)

    elif action == "shorts":
        pairs = _run_suggest_shorts(vtt_files, config, min_s, max_s, platform_max)
        if not pairs:
            return
        vid_id, suggestion = (_pick_suggestion_tty(pairs) if tty else pairs[0])
        _download_clip(vid_id, suggestion, output_format or "mp4", config)

    elif action == "clip":
        pairs = _load_cached_suggestions(config, min_s, max_s, platform_max)
        if not pairs:
            print("Aucune suggestion en cache. Lance d'abord 'shorts' ou 'pipeline'.", file=sys.stderr)
            return
        if tty:
            vid_id, suggestion = _pick_suggestion_tty(pairs)
        else:
            vid_id, suggestion = pairs[0]
            print(f"Sélection automatique (meilleur score) : [{suggestion.score}/5] {suggestion.hook}")
        _download_clip(vid_id, suggestion, output_format or "mp4", config)

    elif action == "pipeline":
        vtt_files = _run_insights(source, config)
        if not vtt_files:
            return
        pairs = _run_suggest_shorts(vtt_files, config, min_s, max_s, platform_max)
        if not pairs:
            return
        if tty:
            vid_id, suggestion = _pick_suggestion_tty(pairs)
        else:
            # Auto-pick highest score
            vid_id, suggestion = max(pairs, key=lambda p: p[1].score)
            print(f"Sélection automatique (score le plus élevé) : [{suggestion.score}/5] {suggestion.hook}")
        _download_clip(vid_id, suggestion, output_format or "mp4", config)
