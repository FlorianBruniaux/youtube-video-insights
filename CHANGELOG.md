# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

**Shorts suggestion pipeline**

- `yt-insights suggest-shorts`: identifies the top 3 Short-worthy moments (30-90s) per VTT file using LLM scoring. Outputs `yt_shorts/<stem>.json` (cache) and `yt_shorts/<stem>.md` (human-readable suggestions with timestamps, hook, score 1-5, verbatim, rationale).
- `yt-insights suggest-shorts --index-only`: regenerates `yt_shorts/INDEX.md` (global table sorted by score across all talks) from existing caches without any LLM call.
- `yt-insights generate-short VIDEO_ID --start HH:MM:SS --end HH:MM:SS`: downloads a single clip segment via `yt-dlp --download-sections`. Only the requested range is fetched (~20-50MB), no full-video download.

**New modules**

- `vtt_parser.py`: timestamped VTT parser complementing `cleaner.py`. Preserves first-occurrence timestamps of each unique text fragment using a `dict[str, float]` dedup strategy. Key functions: `parse_vtt_timestamped()`, `format_timestamped_transcript()`, `ts_to_seconds()`, `seconds_to_hms()`, `youtube_link()`.
- `shorts.py`: full Shorts pipeline. `ShortSuggestion` and `ShortsResult` dataclasses, `suggest_shorts()` (single file), `suggest_all()` (ThreadPoolExecutor batch), `generate_index()` (cross-talk markdown table), `generate_short_clip()` (yt-dlp subprocess).

**Config**

- Added `shorts_dir` (default: `yt_shorts/`) and `shorts_clips_dir` (default: `yt_shorts_clips/`) to `Config` dataclass.
- Added `YT_INSIGHTS_SHORTS_DIR` and `YT_INSIGHTS_SHORTS_CLIPS_DIR` env vars and TOML keys.

---

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
