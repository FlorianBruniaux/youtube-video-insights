# YouTube Catalog Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import or discover YouTube videos into an idempotent local SQLite catalog and retrieve them through a tested FTS5 CLI.

**Architecture:** Add one synchronous domain module owning SQLite schema, transactions, imports, error history, and FTS indexing. Preserve the existing downloader API while adding a structured discovery result. Click commands remain thin adapters over domain functions.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, SQLite FTS5, Click, existing `yt-dlp` subprocess boundary, `unittest`.

**Spec:** `docs/superpowers/specs/2026-08-26-youtube-newsletter-watch-design.md`

## Global Constraints

- Work only in `/Users/florianbruniaux/Sites/perso/youtube-video-insights` and read the external corpus without modifying it.
- Never commit generated corpus data, transcripts, insights, database files, or secrets.
- Use `video_id` as the canonical video identity and preserve source/language variants as artifacts.
- Every external or item-level failure must be persisted in `collection_errors`.
- Every import/discovery is relaunchable and must not create duplicate domain rows.
- No network or LLM call is allowed in the automated test suite.
- Preserve existing `clean_vtt()`, `parse_title()`, and JSON schema behavior.
- Do not commit, push, or open a pull request.

---

### Task 1: SQLite catalog and idempotent corpus import

**Files:**
- Create: `tests/test_catalog.py`
- Create: `src/yt_insights/catalog.py`

**Interfaces:**
- Produces: `Catalog(db_path: Path)`, `Catalog.import_corpus(root: Path) -> RunSummary`, `Catalog.stats() -> CatalogStats`, `Catalog.search(query: str, *, source: str | None, limit: int) -> list[SearchResult]`.
- Produces: `RunSummary(run_id, status, items_seen, items_written, error_count)` and immutable result dataclasses.
- Consumes: `clean_vtt(Path) -> str` without modifying cleaner behavior.

- [ ] **Step 1: Write failing schema and idempotence tests**

Create a temporary corpus with one video represented by French and English
insight/VTT pairs. Assert that the first import creates one `videos` row, one
`video_sources` row, and four language artifacts; a second import leaves those
counts unchanged and creates a second run row.

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_catalog.CatalogImportTests.test_import_is_idempotent_across_language_variants -v`

Expected: import error for missing `yt_insights.catalog`.

- [ ] **Step 3: Implement schema creation and transactional upserts**

Create tables `schema_meta`, `videos`, `video_sources`, `artifacts`,
`ingestion_runs`, and `collection_errors`; create `video_search` with FTS5. Set
`PRAGMA foreign_keys=ON`, `journal_mode=WAL`, and `busy_timeout=60000`. Parse the
existing `YYYYMMDD - Title [videoID].lang.ext` contract. Hash raw artifact bytes,
store searchable text, and use unique constraints for deduplication.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `PYTHONPATH=src python3 -m unittest tests.test_catalog.CatalogImportTests.test_import_is_idempotent_across_language_variants -v`

Expected: one passing test.

- [ ] **Step 5: Add failing error-continuation and FTS tests**

Add fixtures with malformed JSON plus a valid transcript phrase. Assert that the
run becomes `partial`, the malformed path appears in `collection_errors`, the
valid video remains searchable by title, insight subject, transcript phrase, and
source filter, and punctuation in user queries does not trigger SQLite syntax
errors.

- [ ] **Step 6: Run the new tests and verify behavioral failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_catalog -v`

Expected: failures for missing error recording and search behavior.

- [ ] **Step 7: Implement error rows, per-video FTS rebuild, safe query tokenization, stats, and search results**

Flatten insight values defensively, continue after item errors, aggregate artifact
text per video, quote extracted query tokens with `AND`, order by `bm25`, and
return typed results with title/date/sources/watch URL/highlight.

- [ ] **Step 8: Run catalog tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_catalog -v`

Expected: all catalog tests pass with no warning or error output.

### Task 2: Structured discovery errors without breaking existing callers

**Files:**
- Modify: `src/yt_insights/downloader.py`
- Modify: `tests/test_catalog.py`
- Create: `tests/test_downloader.py`

**Interfaces:**
- Produces: `VideoListResult(videos: list[VideoInfo], errors: list[str], returncode: int)`.
- Produces: `fetch_video_list(source: str, *, cookies_from_browser: str | None = None) -> VideoListResult`.
- Preserves: `list_videos(...) -> list[VideoInfo]` as a compatibility wrapper.
- Consumes: `Catalog.ingest_discovery(source, result) -> RunSummary`.

- [ ] **Step 1: Write failing subprocess parsing tests**

Patch only `subprocess.run`, the unavoidable external boundary. Feed one valid
metadata line plus stderr error output and assert that `fetch_video_list` returns
both the parsed video and traceable error. Assert the compatibility wrapper still
returns the video list.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_downloader -v`

Expected: import error for `fetch_video_list` or `VideoListResult`.

- [ ] **Step 3: Implement structured list result and compatibility wrapper**

Parse `ERROR` lines from stderr, preserve the subprocess return code, and add a
fallback message when the process fails without an error line. Do not raise from
the downloader; the catalog decides whether a run is partial or failed.

- [ ] **Step 4: Run downloader tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_downloader -v`

Expected: all downloader tests pass.

- [ ] **Step 5: Write failing catalog discovery test**

Create a `VideoListResult` with two videos and one error, ingest it twice, and
assert two canonical video rows, two source rows, stable counts, two run rows,
and a persisted error for each partial run.

- [ ] **Step 6: Implement `Catalog.ingest_discovery` and run catalog tests**

Run: `PYTHONPATH=src python3 -m unittest tests.test_catalog -v`

Expected: all catalog tests pass.

### Task 3: CLI vertical slice

**Files:**
- Create: `tests/test_cli_catalog.py`
- Modify: `src/yt_insights/cli.py`

**Interfaces:**
- Produces commands `catalog import-corpus`, `catalog discover`, `catalog search`, `catalog stats`, and `catalog errors`.
- All commands accept `--db PATH`, defaulting to `output/catalog.sqlite3`.
- `catalog search` accepts `--source SLUG` and `--limit INTEGER`.

- [ ] **Step 1: Write failing CLI import/search/stats tests**

Use `click.testing.CliRunner` and a fixture corpus. Assert successful import
summary, stable second-import counts, search output containing video ID/title,
machine-readable numeric stats, and persisted error output. Assert an empty
search query exits with a usage error.

- [ ] **Step 2: Run and verify failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_cli_catalog -v`

Expected: no such command `catalog`.

- [ ] **Step 3: Add thin Click command adapters**

Open/close `Catalog` through a context manager, convert domain failures to
`click.ClickException`, print run ID/status/counters, and keep SQL out of CLI
handlers. `catalog discover` calls `fetch_video_list` then
`Catalog.ingest_discovery`.

- [ ] **Step 4: Run CLI and full tests**

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Expected: all tests pass.

### Task 4: User documentation and real-corpus verification

**Files:**
- Modify: `README.md`
- Local generated file only: `output/catalog.sqlite3` (gitignored)

**Interfaces:**
- Documents the four catalog commands, local database location, corpus import,
  idempotence, error visibility, discovery limits, and no-secret rule.

- [ ] **Step 1: Add README command examples and schema behavior**

Document:

```bash
yt-insights catalog import-corpus /Users/florianbruniaux/Sites/perso/yt-insights/output
yt-insights catalog discover https://www.youtube.com/@PragmaticEngineer/videos
yt-insights catalog search "AI product discovery" --source tpc
yt-insights catalog stats
```

- [ ] **Step 2: Run syntax, tests, and CLI help verification**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src tests`

Run: `PYTHONPATH=src python3 -m unittest discover -s tests -v`

Run: `PYTHONPATH=src python3 -m yt_insights.cli catalog --help`

Expected: exit 0 for all three commands and all tests pass.

- [ ] **Step 3: Import the real corpus once and capture counts**

Run: `PYTHONPATH=src python3 -m yt_insights.cli catalog import-corpus /Users/florianbruniaux/Sites/perso/yt-insights/output --db output/catalog.sqlite3`

Expected: run status `completed` or `partial`; any partial status includes a
non-zero queryable error count. The external corpus remains unchanged.

- [ ] **Step 4: Import the real corpus a second time and prove idempotence**

Run the same command again, then run `catalog stats`. Expected: canonical video,
source, and artifact counts exactly match the first import; only run count grows.

- [ ] **Step 5: Run representative searches**

Run searches for `Claude Code`, `product discovery`, and `Martin Fowler` with a
limit of five. Expected: each command exits 0; at least the first and third return
known corpus results, while a genuine zero-result query is explicitly reported.

- [ ] **Step 6: Verify repository scope and diff**

Run: `rtk git status --short --branch`

Run: `rtk git diff --check`

Run: `rtk git diff --stat`

Expected: only planned files in the target repository are changed; no corpus,
database, secret, commit, push, or PR appears.
