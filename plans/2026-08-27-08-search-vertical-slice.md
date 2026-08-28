# Phase 1A execution plan: local search vertical slice

**Status:** approved for implementation on 2026-08-27

**Spec authority:** `plans/2026-08-27-CONSOLIDATED-v2.md`

**Implementation worktree:** `/private/tmp/yt-insights-search-baseline`

**Branch:** `codex/search-baseline`

## Objective

Deliver a deterministic, source-backed FTS5 search slice over at most 50 VTT files. Do not index the full corpus, add MCP, research packs, embeddings, graph storage, or a web UI in this plan.

## Global constraints

- Follow TDD: each production behavior gets a failing test before implementation.
- Use only Python stdlib and existing project dependencies. No new dependency.
- Corpus files are read-only and remain the source of truth.
- The SQLite database is derived, disposable, and rebuilt outside its active path before `os.replace()`.
- Store source paths relative to the corpus root. Never persist machine-specific absolute paths.
- Do not follow a symlink whose resolved target is outside the corpus root.
- Use bound SQL parameters. Never pass raw user input directly to FTS5 `MATCH`.
- IDs and ordering are deterministic for unchanged input.
- Every returned hit includes a source path, video identity, timestamp, and YouTube URL.
- Keep the CLI thin. Corpus, chunking, indexing, and search behavior live under `src/yt_insights/search/`.
- Do not modify the primary checkout or its protected dirty files.
- Do not implement any feature from the deferred roadmap.

### Task 1: Freeze search domain contracts

**Files:**

- Create: `src/yt_insights/search/__init__.py`
- Create: `src/yt_insights/search/models.py`
- Create: `tests/search/test_models.py`

**Required behavior:**

- Define immutable typed records for a source document, a passage, a search query, a search hit, and a build report.
- A document identity distinguishes channel, video, and language variants.
- A passage carries deterministic identity, document identity, ordinal, start/end seconds, text, and timestamped YouTube URL.
- A query supports text, optional channel/language filters, and a bounded result limit with default 10 and maximum 20.
- Reject empty query text, negative timestamps, end before start, empty source text, and invalid limits.
- Keep persistence details out of the records.

**Verification:**

- Targeted model tests pass.
- Complete existing suite passes.
- `git diff --check` passes.

### Task 2: Build deterministic corpus manifest and passages

**Files:**

- Create: `src/yt_insights/search/corpus.py`
- Create: `src/yt_insights/search/chunker.py`
- Create: `tests/search/test_corpus.py`
- Create: `tests/search/test_chunker.py`
- Add focused VTT fixtures only under `tests/search/fixtures/`.

**Required behavior:**

- Discover `*.vtt` recursively in deterministic relative-path order.
- Support an optional deterministic source limit used to select at most 50 files.
- Classify invalid files explicitly instead of dropping them silently.
- Derive channel slug from the directory containing `transcripts/`.
- Parse video ID, title, and language from the current filename convention.
- Hash each source using SHA-256.
- Reuse the existing timestamped VTT parser for raw segments.
- Build passages aligned to available timestamps, targeting 100 to 220 words and 45 to 90 seconds, with bounded overlap.
- A short transcript still produces one passage.
- Produce stable document and passage IDs from canonical source data.
- Never modify source files.

**Verification:**

- Targeted corpus and chunker tests pass.
- Two scans of identical fixtures yield identical manifests and passages.
- A symlink escaping the corpus root is rejected.
- Complete suite and `git diff --check` pass.

### Task 3: Add atomic SQLite FTS5 index and search service

**Files:**

- Create: `src/yt_insights/search/sqlite_fts.py`
- Create: `src/yt_insights/search/query.py`
- Create: `src/yt_insights/search/service.py`
- Create: `tests/search/test_sqlite_fts.py`
- Create: `tests/search/test_query.py`
- Create: `tests/search/test_service.py`

**Required behavior:**

- Use stdlib `sqlite3` with `documents`, `passages`, FTS5 content, and index metadata.
- Build into a sibling temporary database, run integrity and count checks, then publish with `os.replace()`.
- Preserve an existing active database when a build fails.
- Tokenize user text and quote safe terms before `MATCH`; punctuation and FTS operators must not alter the grammar.
- Search with bound parameters and optional exact channel/language filters.
- Return ranked `SearchHit` records with timestamped source data.
- Missing or invalid databases return explicit domain errors.
- No network, LLM, or content logging.

**Verification:**

- Targeted database, query, and service tests pass.
- Simulated failed build leaves the previous database readable.
- Hostile query cases include quotes, hyphens, colons, `NEAR`, `OR`, `*`, empty text, and Unicode.
- Complete suite and `git diff --check` pass.

### Task 4: Expose the 50-video slice through the CLI

**Files:**

- Create: `src/yt_insights/cli_search.py`
- Modify: `src/yt_insights/cli.py`
- Create: `tests/search/test_cli_search.py`
- Modify: `README.md`

**Required behavior:**

- Add `yt-insights index` with `--corpus-root`, `--database`, `--limit`, `--dry-run`, and `--status`.
- Default the slice limit to 50 for this plan. A value above 50 is rejected until the phase 1A gate is accepted.
- `--dry-run` reports discovered, selected, invalid, document, and passage counts without creating a database.
- Add `yt-insights search QUERY` with `--database`, `--channel`, `--lang`, `--limit`, and `--json`.
- Text output includes channel, video title, language, excerpt, timestamp, URL, and relative source path.
- JSON output is deterministic and contains the same semantic fields.
- CLI errors use non-zero exit status and actionable messages.
- Document the vertical-slice commands and its 50-file ceiling.

**Verification:**

- CLI tests cover dry run, build, search, filters, JSON, missing index, hostile query, and the 50-file ceiling.
- Complete suite passes.
- `git diff --check main...HEAD` passes.

### Task 5: Run technical phase 1A verification

**Files:**

- Create: `plans/evidence/2026-08-27-search-vertical-slice.md`

**Required behavior:**

- Select 50 real VTT files deterministically from the local corpus.
- Build the slice database outside the corpus source directories.
- Run two identical builds and compare document/passages counts and frozen queries.
- Run 20 hostile queries and record errors, if any.
- Verify every returned result has an existing relative source, video ID, timestamp, and URL.
- Record build duration, database size, query latency sample, counts, commands, and exit codes.
- Do not claim the editorial relevance gate. It remains `UNKNOWN` until the user supplies three real article subjects and reviews 20 expected results.

**Verification:**

- Fresh full suite passes.
- Evidence file contains the exact corpus root, index path, source count, passage count, commands, and results.
- Primary checkout status still contains the pre-existing protected changes.
- No corpus source hash changes during the run.

## Stop boundary

After Task 5, stop before full-corpus indexing, MCP, or research-pack implementation. Phase 1B is blocked until the user validates three real article scenarios and the 16/20 top-five relevance gate.
