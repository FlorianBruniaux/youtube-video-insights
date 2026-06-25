# CLAUDE.md

Instructions for Claude Code when working in this repository.

## What This Repo Is

`yt-insights` is a Python CLI tool that downloads YouTube channel subtitles via yt-dlp, cleans the VTT format, and extracts structured insights (JSON + Markdown) using any local or cloud LLM. It is a standalone package intended for open-source release.

---

## Directory Map

| Path | What it is |
|---|---|
| `src/yt_insights/` | Package source. One file per concern. |
| `src/yt_insights/cleaner.py` | VTT cleaning (no timestamps). Validated on 47 real videos. Do not touch. |
| `src/yt_insights/vtt_parser.py` | Timestamped VTT parsing for Shorts pipeline. First-occurrence dedup. |
| `src/yt_insights/analyzer.py` | Per-video insight extraction. JSON-first, atomic writes. |
| `src/yt_insights/shorts.py` | Shorts suggestion pipeline. Same patterns as analyzer.py. |
| `src/yt_insights/reporter.py` | Aggregate report generation. |
| `src/yt_insights/downloader.py` | yt-dlp subprocess wrapper. Never import yt-dlp as library. |
| `src/yt_insights/backends/` | LLM backends: base Protocol, openai_compat (Anthropic+OpenAI SSE), mlx |
| `src/yt_insights/config.py` | 4-layer config merge. Includes shorts_dir and shorts_clips_dir. |
| `src/yt_insights/cli.py` | Click CLI. Commands: run, list, report, suggest-shorts, generate-short, config. |
| `yt_transcripts/` | Downloaded VTT files. Gitignored, never commit. |
| `yt_insights/` | Generated JSON + MD insight files. Gitignored, never commit. |
| `yt_shorts/` | Shorts suggestion JSON + MD files. Gitignored, never commit. |
| `yt_shorts_clips/` | Downloaded video segments. Gitignored, never commit. |
| `pyproject.toml` | Setuptools build config, dependencies |
| `CHANGELOG.md` | Version history, kept up to date on every release |

---

## Key Architecture Decisions

**JSON-first insights**: `<video>.json` is the source of truth. The `.md` file is rendered from it. Never write `.md` without writing `.json` first.

**Atomic writes**: always `<name>.tmp.json` → `os.replace()` → `<name>.json`. Never write directly to the final path. Same for `.md`. This ensures a Ctrl-C leaves orphan `.tmp` files at worst, never a corrupt final file.

**stop_reason gate**: if the LLM returns `stop_reason == "max_tokens"`, the response was truncated. Do NOT write the insight file. The next run will retry.

**Sync concurrency**: `ThreadPoolExecutor`, not asyncio. `httpx.Client` is thread-safe. Concurrency defaults: 3 for remote APIs, 1 for Ollama/MLX (they serialize internally).

**Backend auto-detect order**: cc-bridge (port 4141) → Ollama (port 11434) → `ANTHROPIC_API_KEY` env var → `BackendNotFoundError`. Override any of these via `--base-url` and `--model` flags.

---

## cc-bridge Model ID Gotcha

When using cc-bridge, use the gateway format:

```
anthropic/github_copilot/gpt-5-mini
```

A plain model ID (e.g. `claude-haiku-4-5`) triggers cc-bridge's `active_route`, which may forward `x-api-key` to Anthropic in OAuth passthrough mode and return 401.

---

## Dev Setup

```bash
cd ~/Sites/perso/yt-insights
python3 -m venv .venv
.venv/bin/pip install -e "."
.venv/bin/yt-insights --help
```

---

## Shorts Pipeline Architecture

`vtt_parser.parse_vtt_timestamped()` complements `cleaner.clean_vtt()`: the cleaner discards timestamps (used for insight extraction), while the parser preserves first-occurrence timestamps (used for Shorts to give the LLM actionable time references).

The dedup strategy in both modules is intentionally different. `cleaner.py` uses a `set` for fast membership testing. `vtt_parser.py` uses a `dict[str, float]` mapping text to its first timestamp, so the sort at the end recovers chronological order.

`shorts.py` follows the same patterns as `analyzer.py`:
- Atomic writes via `.tmp.json` → `os.replace()`
- `stop_reason == "max_tokens"` gate before any cache write
- One retry with a simplified prompt on JSON parse failure
- `ThreadPoolExecutor` concurrency, inheriting `effective_concurrency()` from config

The Shorts LLM output is a JSON array (not an object like insights). The prompt asks for exactly 3 items scored 1-5; `shorts.py` keeps only the top 3 by score after parsing.

`generate_short_clip()` calls `yt-dlp --download-sections "*HH:MM:SS-HH:MM:SS"`. Only the requested time range is downloaded, no full-video cache needed.

---

## What NOT to Change

- **Never modify `clean_vtt()` or `parse_title()`** without testing on real VTT files. These functions were validated on 47 real videos from `@DevWithAIYoutube`. Any change to the dedup set logic or regex will silently produce different transcripts.
- **Never modify `parse_vtt_timestamped()`** without testing on real VTT files. Rolling caption dedup depends on exact ordering of the `seen` dict; Python 3.7+ insertion order is the guarantee.
- **Never change the insight JSON schema keys** (`subject`, `key_points`, `tools`, `advice`, `quotes`) without updating both the prompt in `analyzer.py` and the `_INSIGHT_KEYS` validation set.
- **Never change the Shorts JSON schema keys** (`start`, `end`, `score`, `hook`, `verbatim`, `rationale`) without updating the prompt in `shorts.py`, `ShortSuggestion.from_dict()`, and `generate_index()`.
- **Never commit `yt_transcripts/`, `yt_insights/`, `yt_shorts/`, or `yt_shorts_clips/`**: data directories are gitignored. They can be gigabytes on large channels.
- **Never make the downloader import `yt_dlp` as a library**: use subprocess only. The yt-dlp API is unstable and the subprocess interface is the stable contract.

---

## Machine-Readable Documentation

Five structured reference files for AI agents and automated tooling:

| File | Purpose |
|------|---------|
| `@docs/machine-readable/llms.txt` | Comprehensive quick reference: module map, constraints, decision tree |
| `@docs/machine-readable/code-map.yaml` | Module map, exports, dependencies, data directories |
| `@docs/machine-readable/adr-index.yaml` | Architecture Decision Records (10 ADRs) |
| `@docs/machine-readable/constraints.yaml` | Forbidden + required patterns, settled decisions, open tensions |
| `@docs/machine-readable/tech-decisions.yaml` | Technology stack choices and rationale |

Load `llms.txt` in CLAUDE.md for a full context snapshot. Load individual YAML files when working on a specific aspect (e.g. `code-map.yaml` when navigating the codebase, `constraints.yaml` when reviewing a PR).

---

## Conventions

- Language: English (code, comments, docstrings, README, commit messages)
- No type stubs or `.pyi` files needed (inline annotations only)
- Commits follow conventional commits format (`feat:`, `fix:`, `chore:`, etc.)
- `CHANGELOG.md` follows Keep a Changelog format, updated on every release
