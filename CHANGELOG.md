# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [Unreleased]

### Added

- Timestamped transcript search with `yt-insights index`, `index --all`,
  `index --status`, and `yt-insights search`. Results include bounded excerpts,
  language and channel filters, timestamps, and direct YouTube links.
- Full-corpus indexing with a disk-capacity preflight, representative 50-file
  selection, deterministic passage identities, and atomic SQLite publication.
- Read-only MCP server with four closed-world tools: `list_corpora`,
  `search_videos`, `search_passages`, and `get_passage`. Catalog and passage
  search keep their separate SQLite sources.
- Agent-facing `doctor --json`, `acquire`, and `export video` commands. Doctor
  is secret-safe and no-write; multi-video acquisition requires confirmation;
  export emits VTT, cleaned text, or sourced timestamped Markdown without an
  LLM.
- One configurable `data_root` that derives transcript, insight, export,
  catalog, and search paths independently of the caller's working directory.
- Optional `mcp` dependency, `yt-insights-mcp` entrypoint, versioned `uv.lock`,
  clean-source wheel smoke test, and reproducible search benchmark script. The
  wheel smoke now installs both minimal and MCP variants outside the checkout,
  exercises the five agent-facing commands, and calls all four MCP tools.
- SHA-256 generation receipts for derived search indexes. Each process validates
  the database content on first access and invalidates its cache when the file
  identity or `ctime` changes.
- Implementation status document with the delivered architecture, conditional
  roadmap, Mermaid diagram, and layered test commands.
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

- Backend resolution now keeps endpoint and model intent separate, honors an
  explicitly requested Ollama model, rejects unavailable models with an
  actionable error, and falls back after failed cc-bridge probes.
- Backend and `config show` diagnostics remove URL userinfo, query strings, and
  fragments before printing endpoints.
- CLI and wizard now report `USED/TOTAL` transcript characters before each real
  LLM call and mark the 10,000-character truncation boundary. Cache hits remain
  silent and perform no generation call.
- Flat transcript layouts now use validated yt-dlp `.info.json` sidecars to
  preserve channel identity instead of collapsing unrelated videos together.
- Removed hardcoded absolute paths (`/Users/florianbruniaux/Sites/perso/yt-insights`) from `.claude/agents/yt-video-analyst.md` and `.claude/skills/yt-get-transcript.md`. Skills now assume only "run from the repo root," making them portable to any machine.
- Reverted a regression that had leaked a specific channel slug (`output/aidevcon/...`) into `.claude/skills/yt-get-insights.md` and `yt-get-shorts.md`, `README.md`, `examples/prompt-claude-code.md`. Both skills now agree on the generic `output/transcripts/` and `output/insights/` paths.
- Corrected the "For development" clone command in `README.md`: the repository is `youtube-video-insights`, not `yt-insights`.
- `/yt-add-channel`'s LLM backend check no longer assumes Ollama exclusively; it now also checks for `ANTHROPIC_API_KEY` before reporting no backend available.

### Documented

- Added a consolidated Claude Code and Codex architecture plus executable plans
  for an agent-ready runtime, portable skills, native agents, read-only MCP,
  digest-bound global installation, and a conditional hosted extension.
- Added parallel ownership, dependency and acceptance gates for the new agent
  integration lot. The documents do not install or alter global configuration.
- README, installation guide, roadmap, changelog, plan index, and `llms.txt`
  now distinguish `catalog.sqlite3` from the timestamped `search-v1.sqlite3`
  index.
- README, installation guide, changelog, roadmap, and `llms.txt` now document
  data-root precedence, acquisition confirmation, export formats, MCP database
  variables, explicit backend scope, and the absence of audio transcription.
- The full-corpus evidence records 3,270 documents, 183,789 passages, a
  48.75-second build, 13.81 ms warm p95, and 258 passing tests on the measured
  development snapshot. Editorial relevance remains unreviewed.
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
