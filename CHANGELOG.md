# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- Local SQLite watch catalog with five new commands: `catalog import-corpus`,
  `catalog discover`, `catalog search`, `catalog stats`, and `catalog errors`.
  The catalog stores canonical videos, source membership, transcript/insight
  artifacts, ingestion runs, and collection errors without requiring an LLM.
- Idempotent corpus ingestion: videos are unique by YouTube ID, language
  variants remain distinct artifacts, and identical content collapses by
  SHA-256. Unchanged imports skip VTT cleaning and FTS reindexing.
- SQLite FTS5 search across titles, sources, insight text, and cleaned
  transcripts, with optional source filtering and highlighted matches.
- Durable `yt-dlp` discovery diagnostics. Partial discoveries persist the
  videos that succeeded alongside the exact collector errors.
- Automated catalog regression suite covering import rollback, malformed
  inputs, validation errors, idempotence, discovery, CLI behavior, and FTS5.
- Tracked `llms.txt` quick reference plus the MVP architecture and phased
  implementation plan under `docs/superpowers/`.
- `INSTALL.md`: full backend setup guide for machines without a local LLM, covering Anthropic API key, Ollama, cc-bridge, and any other OpenAI-compatible provider (Gemini example included), plus a copy-paste prompt to delegate the whole setup to a Claude Code session.
- Skill `/yt-add-channel` (`.claude/skills/`): processes an entire YouTube channel into `output/<slug>/`, then rebuilds the global catalog via `scripts/build_index.py`. Documented in README's skill table (previously missing).

### Fixed

- Removed hardcoded absolute paths (`/Users/florianbruniaux/Sites/perso/yt-insights`) from `.claude/agents/yt-video-analyst.md` and `.claude/skills/yt-get-transcript.md`. Skills now assume only "run from the repo root," making them portable to any machine.
- Reverted a regression that had leaked a specific channel slug (`output/aidevcon/...`) into `.claude/skills/yt-get-insights.md` and `yt-get-shorts.md`, `README.md`, `examples/prompt-claude-code.md`. Both skills now agree on the generic `output/transcripts/` and `output/insights/` paths.
- Corrected the "For development" clone command in `README.md`: the repository is `youtube-video-insights`, not `yt-insights`.
- `/yt-add-channel`'s LLM backend check no longer assumes Ollama exclusively; it now also checks for `ANTHROPIC_API_KEY` before reporting no backend available.

### Documented

- README, Claude Code instructions, `/yt-add-channel`, and the channel-routing
  hook now include the local SQLite catalog workflow.
- README's Backends table now flags that `MLXBackend` (`backends/mlx.py`) is not wired into `resolve_backend()` despite being documented as available via `--base-url mlx`. Not fixed yet, only surfaced so it isn't relied upon by mistake.

---

## [0.2.0] - 2026-06-25

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

**Claude Code integration**

- Agent `yt-video-analyst` (`.claude/agents/`): dispatches on any YouTube URL in Claude Code. Checks `output/transcripts/`, `output/insights/`, `output/shorts/` for existing cache, asks what is needed, invokes the matching skill. Never downloads a clip without user confirmation.
- Skill `/yt-get-transcript` (`.claude/skills/`): downloads the VTT via yt-dlp, cache-aware, retries with browser cookies on HTTP 429.
- Skill `/yt-get-insights`: runs `yt-insights run --skip-download`, reads from cache when already processed, presents subject, key points and notable quote.
- Skill `/yt-get-shorts`: suggests the top 3 Short moments, presents each option with timestamps and verbatim, asks for choice, downloads the selected clip as mp4.
- Skill `/yt-run-pipeline`: chains transcript, insights and Shorts selection in sequence.

### Fixed

- `generate_short_clip()`: replaced `--quiet` with `--no-warnings` in the yt-dlp subprocess call. `--quiet` suppressed ffmpeg stdin handling and caused mp4 mux to fail with exit code 8. mp4 clips now generate reliably.
- Shorts prompt now enforces sentence boundaries: `start` must coincide with the beginning of a complete sentence and `end` with the end of one. Clips no longer cut mid-sentence.

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
