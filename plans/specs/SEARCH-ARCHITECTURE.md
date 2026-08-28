# Search Architecture Specification

**Status:** Approved for implementation planning

**Date:** 2026-08-27

**Owner:** yt-insights maintainers

**Decision:** FTS5-first, VTT-first, local-first, with semantic search behind measured gates

## 1. Objective

Add corpus-wide search to `yt-insights` so a user can find the relevant passage in any indexed video and open YouTube at the matching timestamp. The first production implementation is local, deterministic, rebuildable, and requires neither an LLM nor a network service.

## 2. Product boundaries

### In scope

- Discover and classify the complete local corpus.
- Parse timestamped YouTube VTT files.
- Build deterministic passages from transcript segments.
- Enrich passages with available insight metadata.
- Index passages with SQLite FTS5 and BM25.
- Search by free text with channel, language, date, and source filters.
- Group language variants by video by default.
- Return snippets, timestamps, source provenance, and YouTube deep links.
- Measure relevance, latency, index size, and build reliability.
- Generate timestamped questions as independent versioned sidecars.
- Experiment with dense and hybrid retrieval only after the lexical baseline is measured.

### Explicitly out of scope for the first release

- Qdrant or another remote vector database.
- A hosted multi-user API.
- A Chrome extension.
- A Next.js, PocketBase, or Elysia application.
- Rewriting the existing insight JSON corpus.
- Sending transcripts or search queries to a remote service.
- Treating IFTTD Markdown files as timestamped YouTube videos.

## 3. Verified corpus snapshot

The implementation must not hard-code these counts, but the initial dry-run must reconcile them:

| Source | Files | Unique identities | Rule |
|---|---:|---:|---|
| Insight JSON | 3,219 | 3,083 video IDs | Metadata and enrichment |
| YouTube VTT | 3,270 | 3,130 video IDs | Primary timestamped search source |
| Multilingual VTT variants | 140 extra files | 140 video IDs | Preserve each language; group results |
| IFTTD Markdown | 356 | Non-YouTube episode identities | Classify as unsupported in P0 |

The VTT corpus is approximately 1,005.5 MiB. A benchmark on the smaller insight JSON corpus built an in-memory FTS5 index in approximately 496 ms and answered sample queries in 0.04–0.26 ms. Those measurements validate SQLite feasibility but do not constitute the transcript-search performance baseline.

## 4. Architectural decisions

### ADR-1: Local SQLite FTS5 is the first retrieval engine

Reasons:

- Python already ships with SQLite and the local runtime has FTS5 enabled.
- The corpus is local and single-user today.
- The index remains a disposable derivative of source files.
- FTS5 provides BM25, snippets, highlighting, Unicode tokenization, and structured joins.
- One versioned file is straightforward to back up, replace atomically, and roll back.

This decision is not a permanent ban on embeddings. It establishes the lexical baseline that semantic approaches must beat.

### ADR-2: VTT is the primary search source

Insight summaries are useful metadata but cannot provide exhaustive passage coverage or reliable timestamps. Each searchable YouTube document is anchored in a VTT file. Insight fields may be indexed as weighted enrichment.

### ADR-3: Source files remain immutable inputs

The search pipeline reads the corpus and writes only under `output/.search/`. It does not normalize, rename, or rewrite VTT, insight, or Markdown sources.

### ADR-4: The index is derived and atomically replaceable

Build into a temporary sibling database, validate it, then publish it with `os.replace()`. Never delete the active index because a schema, tokenizer, dimension, or model changed.

### ADR-5: Language variants are distinct documents

The stable identity is `(source_type, media_id, language)`. French and English variants are stored independently. Search results are grouped by `(source_type, media_id)` by default, and `--all-languages` exposes every matching variant.

### ADR-6: User input does not become raw FTS grammar

SQL parameter binding prevents SQL injection but does not neutralize FTS operators inside `MATCH`. The query normalizer accepts text, extracts bounded Unicode terms, quotes each term, and constructs the supported grammar itself.

### ADR-7: Search and LLM-generated questions are separate contexts

Question generation writes independent sidecars. Search consumes validated sidecars as an optional passage kind. Failure or absence of question generation never blocks transcript search.

### ADR-8: Unknown provenance remains unknown

Legacy insights lack model, prompt, transcript hash, and generation timestamp. The implementation stores `UNKNOWN` or `null`; it never fabricates historical provenance.

## 5. Module boundaries

```text
src/yt_insights/search/
  __init__.py       Public search-domain exports
  models.py         Immutable domain values and build reports
  corpus.py         Source discovery, identity parsing, and manifest creation
  chunker.py        Deterministic timestamped passage construction
  ports.py          SearchIndex protocol
  sqlite_fts.py     SQLite schema, atomic builds, and lexical retrieval
  service.py        Query normalization, filters, ranking, and grouping
  render.py         Human and JSON output formatting

src/yt_insights/cli_search.py
  Thin Click commands registered by cli.py
```

The existing `cli.py` registers the commands but does not own search-domain logic. The existing `vtt_parser.py` remains the VTT segment parser until a measured defect requires a compatible refactor.

## 6. Stable interfaces

The first implementation uses these names and meanings so parallel sessions can work against one contract:

```python
@dataclass(frozen=True)
class DocumentRef:
    document_id: str
    source_type: Literal["youtube_vtt", "ifttd_markdown"]
    media_id: str
    channel: str
    title: str
    language: str | None
    upload_date: date | None
    source_path: Path
    source_sha256: str
    has_timestamps: bool


@dataclass(frozen=True)
class Passage:
    passage_id: str
    document_id: str
    ordinal: int
    kind: Literal["transcript", "question", "insight"]
    start_seconds: float | None
    end_seconds: float | None
    text: str
    text_sha256: str
    subject: str | None = None
    tools: tuple[str, ...] = ()
    question: str | None = None


@dataclass(frozen=True)
class SearchQuery:
    text: str
    channel: str | None = None
    language: str | None = None
    after: date | None = None
    before: date | None = None
    kinds: tuple[str, ...] = ()
    limit: int = 10
    all_languages: bool = False


@dataclass(frozen=True)
class SearchHit:
    rank: int
    score: float
    document: DocumentRef
    passage: Passage
    snippet: str
    youtube_url: str | None


@dataclass(frozen=True)
class BuildReport:
    discovered: int
    indexed: int
    excluded: int
    invalid: int
    passages: int
    multilingual_groups: int
    duration_seconds: float
    database_bytes: int
    corpus_fingerprint: str
    errors: tuple[str, ...]


class SearchIndex(Protocol):
    def rebuild(
        self,
        documents: Iterable[DocumentRef],
        passages: Iterable[Passage],
        *,
        corpus_fingerprint: str,
        force: bool = False,
    ) -> BuildReport: ...
    def search(self, query: SearchQuery) -> list[SearchHit]: ...
    def status(self) -> BuildReport: ...
```

The exact serialization of these dataclasses may evolve before implementation, but sessions must change the specification first rather than silently inventing incompatible names.

## 7. Corpus and passage rules

### YouTube identity parsing

- Extract `media_id` only from the final `[A-Za-z0-9_-]{11}` bracket token preceding language and `.vtt`.
- Derive language from the suffix such as `.fr.vtt` or `.en.vtt`.
- Store paths relative to the configured corpus root.
- Reject path traversal and symlinks resolving outside the corpus root.
- Use a deterministic document ID derived from source type, media ID, and language.

### Initial chunking policy

- Target 100–220 words per passage.
- Target 45–90 seconds per passage.
- Overlap adjacent passages by 10–15 seconds.
- Align boundaries to parsed VTT segments.
- Never split a single VTT segment.
- Set `start_seconds` to the first included segment.
- Set `end_seconds` to the next segment start or the final included start when duration is unavailable.
- Derive `passage_id` from document ID, ordinal, timestamps, and normalized text hash.

### Insight enrichment

- Match per-video insight JSON to a VTT document by media ID and language.
- Emit one untimestamped `kind="insight"` passage per valid insight file.
- Map `subject` and normalized tool names into their dedicated FTS columns.
- Combine key points, advice, and quotes into the insight passage body without changing the source JSON.
- Classify malformed or orphan insight files explicitly; never silently count them as indexed.
- Insight enrichment is optional for transcript availability: an invalid insight is reported but does not erase a valid VTT document.

### Question enrichment

- Load only schema-valid, grounded question sidecars.
- Emit one `kind="question"` passage per question, using its evidence as body and its question text in the dedicated FTS column.
- A corrupt sidecar produced by yt-insights blocks publication of the next index until regenerated or removed explicitly.

The evaluation plan is allowed to tune these numbers. It must record the chunker version whenever the policy changes.

## 8. SQLite contract

The database contains normal relational tables plus an FTS5 index:

```sql
CREATE TABLE index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE documents (
  id INTEGER PRIMARY KEY,
  document_id TEXT NOT NULL UNIQUE,
  source_type TEXT NOT NULL,
  media_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  title TEXT NOT NULL,
  language TEXT,
  upload_date TEXT,
  source_relpath TEXT NOT NULL UNIQUE,
  source_sha256 TEXT NOT NULL,
  has_timestamps INTEGER NOT NULL CHECK (has_timestamps IN (0, 1))
);

CREATE TABLE passages (
  id INTEGER PRIMARY KEY,
  passage_id TEXT NOT NULL UNIQUE,
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  kind TEXT NOT NULL,
  start_seconds REAL,
  end_seconds REAL,
  text_sha256 TEXT NOT NULL,
  UNIQUE(document_id, ordinal, kind)
);

CREATE VIRTUAL TABLE passages_fts USING fts5(
  title,
  subject,
  tools,
  question,
  body,
  tokenize = "unicode61 remove_diacritics 2 tokenchars '-_'"
);
```

The regular FTS table stores searchable text so `snippet()` and `highlight()` remain available. The relational `passages` table stores identity, filters, timestamps, and hashes without duplicating transcript bodies. The adapter owns synchronization between relational row IDs and FTS row IDs inside one transaction.

Initial BM25 weighting:

```text
title=8, subject=5, tools=4, question=3, body=1
```

Weights are configuration of the index version, not user-controlled query input.

## 9. CLI contract

```text
yt-insights index --dry-run
yt-insights index --rebuild
yt-insights index --status

yt-insights search QUERY
  --channel TEXT
  --lang TEXT
  --after YYYY-MM-DD
  --before YYYY-MM-DD
  --kind transcript|question|insight
  --limit 1..100
  --all-languages
  --json
```

Exit codes:

- `0`: successful build, status, or search, including zero results.
- `1`: invalid source, corrupt index, build validation failure, or missing index.
- `2`: invalid CLI arguments handled by Click.

Search never invokes a backend and never accesses the network.

`SearchHit.score` is retriever-specific diagnostic data. Ordering and grouping use the returned rank, never a direct comparison between BM25, cosine, or fused scores.

## 10. Reliability and security requirements

- Fail closed if any source expected to be indexable cannot be parsed.
- A failed build leaves the previous index and manifest untouched.
- Run `PRAGMA foreign_key_check` and `PRAGMA integrity_check` before publication.
- Open query connections read-only with a SQLite URI using `mode=ro`.
- Bound SQL values and reconstruct supported FTS grammar from safe tokens.
- Bound query length to 500 Unicode code points.
- Bound result limit to 1–100.
- Do not log query text, transcript text, or snippets by default.
- Do not include absolute local paths in the index or JSON output.
- Treat the database as sensitive because it contains transcript text.
- Preserve the old index during a failed migration.

## 11. Acceptance gates

### P0 lexical release

- Complete corpus dry-run reconciles all discovered sources.
- Full VTT build completes in under five minutes on the reference machine.
- Peak RSS remains below 1 GiB.
- Warm search p95 remains below 100 ms on the reference corpus.
- `Recall@5` reaches at least 0.80 on the reviewed evaluation set.
- One hundred hostile queries produce no parser or SQLite error.
- Exact filters return no result outside their allowed values.
- Simulated interruption preserves the previous usable index.
- No source hash changes during index build.

These thresholds are proposed release gates, not claims about current transcript-search performance.

### Hybrid adoption

Adopt dense retrieval only if a held-out evaluation shows at least one of:

- relative `nDCG@10` improvement of 10% or more; or
- absolute `Recall@5` improvement of five points or more.

Additionally:

- exact-query regression stays below two points;
- improvement is present in paraphrase and bilingual slices;
- warm p95 stays below 200 ms;
- embedding cost and cache size are documented;
- vectors are keyed by text hash, model ID, and model revision.

### Qdrant evaluation

Evaluate Qdrant only after hybrid retrieval passes its relevance gate and at least one operational trigger exists:

- approximately one million passages;
- a multi-gigabyte vector index;
- a remote or multi-user service requirement;
- concurrency exceeding the optimized SQLite SLA;
- independent replication, tenant filtering, or availability requirements.

## 12. Migration and rollback

- Index filenames include a schema generation such as `search-v1.sqlite3`.
- Build the next generation beside the active one.
- Validate counts, hashes, foreign keys, and integrity.
- Replace an active local index atomically only after validation.
- Retain the previous generation until the new one passes smoke queries.
- Semantic indexes never mix vector dimensions or model revisions.
- A future Qdrant migration builds an immutable versioned collection, validates it, snapshots it, and swaps an alias. It never drops the active collection first.

## 13. Reuse policy for `more-than-fan`

Reusable concepts:

- corpus-wide search;
- timestamped questions;
- model and prompt provenance;
- synchronized navigation to video moments.

Do not reuse its source files because no tracked license grants reuse and the inspected implementation contains inactive search code, unsafe collection recreation, inconsistent question contracts, unvalidated model output, hard-coded credentials, and unsafe DOM insertion.
