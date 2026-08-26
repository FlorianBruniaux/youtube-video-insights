# YouTube Newsletter Watch MVP Design

**Date:** 2026-08-26

## Purpose

Turn the existing `yt-insights` CLI and its local corpus into a relaunchable,
inspectable watch system for a Tech / Product / AI newsletter. The MVP remains
local, deterministic by default, and exposes one domain layer to both the CLI
and a later MCP server.

## Verified baseline

The repository already lists videos through `yt-dlp --flat-playlist`, downloads
automatic VTT subtitles, cleans rolling captions, extracts a five-field insight
JSON with an LLM, renders Markdown, and builds aggregate reports. JSON insight
writes are atomic and cached.

The existing external corpus at
`/Users/florianbruniaux/Sites/perso/yt-insights/output` contains 3,219 insight
JSON files, 3,270 VTT files, and about 1.0 GB of data. It has 3,083 unique video
IDs among the insights. There are 136 duplicate insight rows by video ID and
140 duplicate VTT rows by video ID, primarily alternate French and English
caption tracks. Twenty insight JSON files have no matching Markdown file. All
3,219 JSON files parse and have the five expected keys, but five values violate
the expected field types.

No test suite, database, full-text search, semantic search, editorial scoring,
newsletter export, or MCP server is versioned today. The checked-in README also
references ignored `docs/machine-readable/*` files that are absent from a clean
checkout.

## Scope

### MVP goals

- Discover videos from a known source and later search YouTube by keyword.
- Import the existing corpus without copying it into Git.
- Store canonical videos once, while preserving multiple source and language
  artifacts.
- Trace every import/discovery run and every item-level collection error.
- Search titles, source names, insight text, and cleaned transcripts with SQLite
  FTS5.
- Add deterministic editorial scoring before any LLM reranking.
- Export a reproducible newsletter candidate selection.
- Expose the same application services through a thin MCP stdio adapter.

### Non-goals

- Multi-agent orchestration.
- Cloud infrastructure or a paid SaaS dependency.
- A web UI, scheduler daemon, or job board in the first MVP.
- A vector database before FTS5 quality has been measured.
- Automatic publication to a newsletter platform.
- Circumventing authentication, access controls, or YouTube restrictions.

## Considered approaches

### 1. Extend the existing package with SQLite (recommended)

Keep download, cleaning, and analysis code. Add a catalog module and expose it
through CLI and MCP adapters. This minimizes migration, preserves the useful
corpus, uses Python's standard `sqlite3`, and gives deterministic transactions,
unique constraints, FTS5, and inspectable provenance.

### 2. Keep files and generate richer JSON/YAML indexes

This is the smallest code change, but it keeps deduplication, concurrent updates,
error history, and querying fragile. Every new view requires another full scan.
It also cannot provide a reliable application boundary for MCP.

### 3. Build a separate RAG/vector service

This would enable semantic retrieval immediately, but adds another service,
embedding lifecycle, versioning, and operational cost before lexical retrieval
has been tested. It duplicates the current package and is unjustified for a
single-user local MVP.

## Chosen architecture

```text
YouTube API / channel RSS / yt-dlp / existing corpus
                         |
                         v
                deterministic collectors
                         |
                         v
                 SQLite catalog + FTS5
                         |
          +--------------+---------------+
          |                              |
          v                              v
       Click CLI                    MCP stdio adapter
          |
          v
 deterministic score -> optional LLM enrichment -> newsletter export
```

The package remains synchronous. Collection is I/O bound and the existing code
already uses subprocesses and `ThreadPoolExecutor`. SQLite writes are serialized
inside short transactions and configured with foreign keys, WAL mode, and a
busy timeout.

## Data model

### `videos`

One row per YouTube video ID. It stores the best-known title, publication date,
watch URL, description, duration, and timestamps for discovery and metadata
refresh. Empty incoming values never erase non-empty values.

### `video_sources`

Many-to-many provenance between videos and source slugs/URLs. This preserves a
talk mirrored in two channel corpora without creating two canonical videos.

### `artifacts`

Transcript and insight artifacts with video ID, source slug, language, absolute
path, SHA-256, searchable text, and import timestamp. The unique key includes
video ID, kind, language, and content hash. Identical files imported from two
locations collapse; materially different language tracks remain available.

### `ingestion_runs` and `collection_errors`

Every import or discovery has start/end timestamps, status, counters, and its
source. Invalid JSON, malformed filenames, VTT cleaning failures, and external
collector errors are item-level rows rather than transient terminal text.

### `video_search`

An FTS5 table with one aggregated document per video: title, sources, subject,
and all searchable artifact text. Reindexing a video is deterministic and can
be repeated after every artifact upsert.

## Data flows

### Existing corpus import

1. Scan only `*/insights/*.json` and `*/transcripts/*.vtt` in sorted order.
2. Parse date, title, video ID, and language from the existing filename contract.
3. Upsert the canonical video and source relation.
4. Validate/read the artifact, compute SHA-256, and insert or reuse it.
5. Record malformed items and continue.
6. Rebuild FTS documents only for touched video IDs.
7. Finish the run with counts and `completed`, `partial`, or `failed` status.

Running the same import twice must keep video, source, and artifact counts
unchanged while adding a separate successful run record.

### Known-source discovery

The first vertical slice reuses the existing `yt-dlp --flat-playlist` collector.
Its subprocess result becomes structured: videos, return code, and error lines.
Discovered video metadata is upserted even when a source returns partial errors;
those errors are attached to the run.

### Search

User text is tokenized and quoted before FTS `MATCH`, avoiding accidental FTS
syntax. Results can be filtered by source slug and are ordered by BM25 followed
by publication date. Search returns canonical video metadata and a short
highlight, not raw database rows.

## Discovery source comparison

### YouTube Data API

Best primary source for keyword search and canonical metadata. As of 2026-08-26,
the current English documentation says a project has 100 `search.list` calls per
day in a dedicated search bucket, with each call costing one unit, plus 10,000
daily units shared by most other endpoints. This changed from the older model
still shown in some translated pages, where one search cost 100 general quota
units, so the live console and English quota page must be treated as canonical.
`channels.list`, `playlistItems.list`, and `videos.list` each cost one general
unit. Known-channel history should therefore use the channel's uploads playlist,
not repeated `search.list` calls. Search is capped at 50 results per page and
channel-filtered searches can be capped at 500 videos.

API data that is cached locally must be refreshed or deleted after 30 days under
the current developer policies. Custom editorial scores must be clearly
separated from YouTube-provided metrics and must not be presented as YouTube
metrics.

Sources:

- https://developers.google.com/youtube/v3/getting-started
- https://developers.google.com/youtube/v3/determine_quota_cost
- https://developers.google.com/youtube/v3/docs/search/list
- https://developers.google.com/youtube/v3/guides/implementation/videos
- https://developers.google.com/youtube/terms/developer-policies

### Channel RSS feeds

`https://www.youtube.com/feeds/videos.xml?channel_id=...` is live and required no
credential in a direct check on 2026-08-26. The checked feed returned 15 entries
with IDs, titles, timestamps, descriptions, thumbnails, and some public counters.
It is excellent for cheap polling of known channels but cannot backfill history,
search globally, or resolve a handle without first obtaining a channel ID. The
endpoint is not documented as a supported YouTube Data API contract, so parser
failures must be visible and the uploads playlist remains the reliable backfill.

### `yt-dlp`

It already works in this repository and provides rich metadata plus automatic
subtitle tracks without a Google API key. It is the pragmatic transcript adapter
for a private local experiment. It is also an unofficial extractor whose own
maintainers describe extractors as fragile because upstream layouts change.

The legal/product risk is real: YouTube's Terms prohibit automated access such
as scrapers except for stated exceptions, and the API developer policies prohibit
scraping YouTube applications or obtaining scraped YouTube data/content. This
design does not claim that personal use makes scraping compliant. Before turning
the MVP into a public or commercial service, obtain legal review or replace this
path with an authorized transcript source. The official Captions API is not a
drop-in solution for third-party public videos: `captions.list` costs 50 units,
requires OAuth authorization, and caption download also requires OAuth.

Sources:

- https://www.youtube.com/t/terms
- https://developers.google.com/youtube/terms/developer-policies
- https://developers.google.com/youtube/v3/docs/captions/list
- https://developers.google.com/youtube/v3/guides/implementation/captions
- https://github.com/yt-dlp/yt-dlp
- https://github.com/yt-dlp/yt-dlp/blob/master/CONTRIBUTING.md

### Web search

Useful for exploratory discovery across websites and for finding videos mentioned
in articles, but ranking, pagination, coverage, cost, and terms depend on the
provider. It should be a manual/fallback collector whose returned URLs are then
normalized and verified, not the canonical ingestion path.

### Other unofficial transcript/search libraries

Libraries built on undocumented YouTube endpoints have the same stability and
terms risks as direct extraction while adding another dependency. They bring no
MVP value over the existing `yt-dlp` subprocess boundary and are rejected.

## Deterministic scoring and LLM boundary

The deterministic score uses explicit features only: configured Tech/Product/AI
keywords, source allowlist/weight, age, transcript availability, duplicate state,
and optional duration bands. The score stores its feature breakdown and scoring
profile version.

An LLM is justified only after deterministic filtering, for:

- assigning editorial categories when keywords are ambiguous;
- extracting named speakers when title/description regexes fail;
- producing a newsletter-oriented summary and proposed angle;
- detecting semantic near-duplicates and repeated claims across videos;
- reranking a small candidate set against an editorial brief.

An LLM is not used for IDs, dates, freshness, exact duplicates, storage,
retries, run state, quota accounting, or export formatting.

## MCP boundary

The MCP server imports application functions from `catalog.py`; it does not run
SQL directly. The first tools are `search_videos`, `get_video`,
`discover_channel`, `list_collection_errors`, and
`export_newsletter_candidates`. Stdio is the default transport. A missing
optional MCP dependency must fail with an actionable installation message while
the CLI remains usable.

## Phased implementation plan

### Phase 1 — local catalog vertical slice

**Files:** `src/yt_insights/catalog.py`, `src/yt_insights/downloader.py`,
`src/yt_insights/cli.py`, `tests/test_catalog.py`, `tests/test_cli_catalog.py`,
`README.md`.

**Behavior:** import the external corpus, discover a source through the existing
collector, deduplicate in SQLite, trace errors, search via FTS5, and show stats.

**Tests:** schema creation, two-pass idempotence, language variants, identical
artifact hashes, invalid JSON continuation, discovery partial failure, FTS title/
subject/transcript matches, source filter, and CLI exit/output behavior.

**Acceptance:** a second corpus import changes no domain counts; one video ID has
one canonical row; all import failures are queryable; a known phrase in a fixture
transcript is returned by CLI search; no network or LLM is required for tests.

**Dependencies/risks:** Python SQLite must include FTS5; full-corpus indexing may
produce a database of several hundred MB; filename parsing is a legacy contract.

### Phase 2 — official discovery and watchlist

**Files:** `src/yt_insights/youtube_api.py`, `src/yt_insights/rss.py`,
`src/yt_insights/watchlist.py`, `src/yt_insights/config.py`, CLI and tests.

**Behavior:** keyword search through Data API, known-channel polling through RSS,
uploads-playlist backfill, metadata refresh timestamps, page checkpoints, and
quota-aware limits. API key comes only from environment/config outside Git.

**Tests:** recorded minimal fixtures for API/RSS parsing, pagination, 15-entry RSS
window, quota exhaustion, 30-day refresh selection, retries, and malformed feeds.

**Acceptance:** no duplicate videos across RSS/API/import; interrupted pagination
resumes; API metadata older than 30 days is selected for refresh; secrets never
appear in logs or Git diff.

**Dependencies/risks:** quota policy can change; RSS is undocumented; public
commercial use needs API policy and privacy review.

### Phase 3 — deterministic editorial pipeline

**Files:** `src/yt_insights/scoring.py`, `src/yt_insights/selection.py`,
`src/yt_insights/exporter.py`, config, CLI and tests.

**Behavior:** versioned score breakdown, candidate lifecycle (`new`, `reviewed`,
`selected`, `rejected`, `published`), duplicate/near-duplicate flags, and Markdown
plus JSON newsletter exports.

**Tests:** fixed scoring fixtures, stable tie-breaking, state transitions, export
schema, and reproduction of an export from stored selection IDs.

**Acceptance:** every score is explainable without an LLM; exports contain source
URL, title, date, channel, rationale, summary, and provenance; reruns are byte-
stable for unchanged data.

**Dependencies/risks:** editorial weights can encode bias; view/like-based custom
scores create policy risk and are excluded by default.

### Phase 4 — optional LLM enrichment and semantic retrieval

**Files:** `src/yt_insights/editorial.py`, `src/yt_insights/embeddings.py`, schema
migration, CLI and tests.

**Behavior:** enrich only shortlisted videos, cache by input hash + prompt version
+ model, validate host-side output, and optionally index local embeddings.

**Tests:** invalid output rejection, retry bounds, truncation gate, cache keys,
embedding model version migration, and deterministic fallback without an LLM.

**Acceptance:** no LLM call for unchanged inputs; invalid responses never replace
valid data; disabling embeddings preserves all lexical search features.

**Dependencies/risks:** model cost/quality drift, multilingual embedding quality,
and false semantic duplicate decisions require human review.

### Phase 5 — MCP adapter

**Files:** `src/yt_insights/mcp_server.py`, optional dependency group and tests.

**Behavior:** stdio MCP tools wrap the same catalog, discovery, selection, and
export functions used by CLI.

**Tests:** tool schema snapshots, read-only search/get calls, explicit write calls,
structured errors, and subprocess stdio smoke test.

**Acceptance:** Codex/Claude can query the local catalog without shell parsing;
MCP and CLI return equivalent video IDs for the same filters; missing optional
dependency does not break normal CLI imports.

**Dependencies/risks:** MCP SDK API drift; concurrent writers require the existing
SQLite busy timeout and short transactions.

## Rollback

Phase 1 creates only code and an ignored local SQLite file. Rollback is deleting
that database and reverting the new modules/CLI wiring. The external corpus is
read-only and never rewritten. Later schema changes use monotonic versioned
migrations and a backup before migration.
