# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

## [0.1.0] - 2026-06-24

Initial release. Extracted and refactored from `boldguy/scripts/youtube_insights.py`
(a single-file POC validated on 47 real videos from `@DevWithAIYoutube`).

### Added

**Core pipeline**

- `yt-insights run SOURCE`: downloads subtitles via yt-dlp, analyzes each video, generates aggregate report
- `yt-insights report`: regenerates aggregate report from existing insight JSON files
- `yt-insights config init`: creates `~/.config/yt-insights/config.toml` with commented defaults

**Analyzer**

- JSON-first insight schema: `subject`, `key_points`, `tools`, `advice`, `quotes`
- Per-video `<stem>.json` (source of truth) and `<stem>.md` (rendered from JSON)
- Atomic writes via `os.replace()` on `.tmp.json` and `.tmp.md`
- `stop_reason` gate: truncated responses (`max_tokens`) are never written to cache
- JSON fence stripping for small models that add ```json blocks
- One retry with simplified prompt on JSON parse failure
- `ThreadPoolExecutor` concurrency: 3 for remote APIs, 1 for Ollama/MLX

**Backends**

- `OpenAICompatBackend`: dual SSE parsing handling both Anthropic and OpenAI wire formats in a single code path
- `MLXBackend`: Apple Silicon optional backend (requires `[mlx]` extra)
- Auto-detection order: cc-bridge (port 4141) -> Ollama (port 11434) -> `ANTHROPIC_API_KEY` -> `BackendNotFoundError`
- `BackendNotFoundError` and `BackendUnavailableError` typed exceptions surfaced to CLI as clean `sys.exit(1)` messages

**Config**

- 4-layer merge: defaults -> `~/.config/yt-insights/config.toml` -> `YT_INSIGHTS_*` env vars -> CLI flags
- `Config.with_url()` helper for backend auto-detect overrides

**Downloader**

- `--print after_move:filepath` flag on yt-dlp for exact file list (no post-run glob)
- `DownloadResult` dataclass with `vtt_files`, `errors`, `skipped_count`
- Optional `--sleep-requests` for rate-limited channels

**Reporter**

- `Counter`-based top-tools aggregation (no LLM)
- Single LLM narrative call on JSON-compacted payload (no 30-video cap)
- Atomic `AGGREGATE_REPORT.md` and `AGGREGATE_REPORT.json`

**Package**

- `src/` layout, setuptools, `pipx`-installable
- `[mlx]` optional extra for Apple Silicon
- Gitignore anchors `/yt_transcripts/` and `/yt_insights/` to root only
