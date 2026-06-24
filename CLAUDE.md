# CLAUDE.md

Instructions for Claude Code when working in this repository.

## What This Repo Is

`yt-insights` is a Python CLI tool that downloads YouTube channel subtitles via yt-dlp, cleans the VTT format, and extracts structured insights (JSON + Markdown) using any local or cloud LLM. It is a standalone package intended for open-source release.

---

## Directory Map

| Path | What it is |
|---|---|
| `src/yt_insights/` | Package source. One file per concern. |
| `src/yt_insights/backends/` | LLM backends: base Protocol, openai_compat (Anthropic+OpenAI SSE), mlx |
| `yt_transcripts/` | Downloaded VTT files. Gitignored, never commit. |
| `yt_insights/` | Generated JSON + MD insight files. Gitignored, never commit. |
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

## What NOT to Change

- **Never modify `clean_vtt()` or `parse_title()`** without testing on real VTT files. These functions were validated on 47 real videos from `@DevWithAIYoutube`. Any change to the dedup set logic or regex will silently produce different transcripts.
- **Never change the JSON schema keys** (`subject`, `key_points`, `tools`, `advice`, `quotes`) without updating both the prompt in `analyzer.py` and the `_INSIGHT_KEYS` validation set.
- **Never commit `yt_transcripts/` or `yt_insights/`**: data directories are gitignored for a reason. They can be gigabytes on large channels.
- **Never make the downloader import `yt_dlp` as a library**: use subprocess only. The yt-dlp API is unstable and the subprocess interface is the stable contract.

---

## Conventions

- Language: English (code, comments, docstrings, README, commit messages)
- No type stubs or `.pyi` files needed (inline annotations only)
- Commits follow conventional commits format (`feat:`, `fix:`, `chore:`, etc.)
- `CHANGELOG.md` follows Keep a Changelog format, updated on every release
