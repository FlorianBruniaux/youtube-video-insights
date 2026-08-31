# Cumulative YouTube Research Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implemented locally; hosted and external gates remain tracked separately

**Goal:** Add a persistent, catalogue-first research workflow that always asks whether local evidence is sufficient, previews fresh YouTube candidates when requested, acquires only approved videos, reassesses the enriched corpus, and exports a versioned evidence dossier.

**Architecture:** A new `yt_insights.research` package owns immutable contracts, objective assessment, a separate SQLite session store, bounded discovery, orchestration, and deterministic dossiers. Existing search, catalogue, acquisition, export, and read-only MCP services remain authoritative for their current responsibilities. Claude Code and Codex call the same `yt-insights research` CLI contract through a new main-session skill.

**Tech Stack:** Python 3.11+, Click, SQLite, FTS5, yt-dlp subprocess adapter, pytest, Ruff, Mypy on `src`, Markdown and JSON evidence artifacts.

**Spec:** `docs/superpowers/specs/2026-08-31-cumulative-research-workflow-design.md`

## Global Constraints

- Local assessment performs no network access and mutates only `research.sqlite3`.
- Every assessment ends in `awaiting_sufficiency_confirmation`; code never decides sufficiency.
- Freshness profiles are exact: `fast=14`, `standard=30`, `stable=90`, `historical=None` days.
- Accept one to eight explicit queries, each at most 500 Unicode code points after trimming.
- Store at most twenty passage hits and twenty video hits per query.
- Discovery returns at most ten candidates and does not mutate corpus, catalogue, or search index.
- Acquisition accepts one to five exact IDs from the latest candidate snapshot.
- `refresh` authorizes discovery only; candidate approval is a separate persisted decision.
- Existing MCP tools and the native corpus researcher remain read-only.
- Generated dossiers never enter the source catalogue or FTS index.
- Agent prompts, skill instructions, and examples are written in English.
- All code tasks use RED, GREEN, REFACTOR and one scoped commit.
- The primary checkout is dirty; every worker uses an isolated worktree and explicit pathspecs.
- Human relevance stays `UNKNOWN` until exactly twenty pilot results are reviewed and at least sixteen receive grade 1 or 2.

---

## File and Ownership Map

| Owner | Files | Responsibility |
| --- | --- | --- |
| Coordinator | `src/yt_insights/research/models.py`, `src/yt_insights/paths.py`, `src/yt_insights/config.py`, `src/yt_insights/cli.py` | Shared contracts, paths, CLI registration |
| Assessment stream | `src/yt_insights/research/assessment.py`, `tests/research/test_assessment.py` | Read-only evidence collection and freshness calculation |
| Store stream | `src/yt_insights/research/store.py`, `tests/research/test_store.py` | Schema, transactions, revisions, idempotency, durable events |
| Discovery stream | `src/yt_insights/research/discovery.py`, `src/yt_insights/downloader.py`, `src/yt_insights/catalog.py`, `tests/research/test_discovery.py`, targeted existing tests | Candidate provider, metadata, deduplication, diversity |
| Coordinator integration | `src/yt_insights/research/workflow.py`, `src/yt_insights/cli_research.py`, `tests/research/test_workflow.py`, `tests/test_cli_research.py` | State-machine orchestration and CLI JSON/human contracts |
| Acquisition stream after integration | `src/yt_insights/research/acquisition.py`, `src/yt_insights/acquisition.py`, `tests/research/test_research_acquisition.py` | Exact approved-ID acquisition and one refresh per batch |
| Dossier stream | `src/yt_insights/research/dossier.py`, `tests/research/test_dossier.py`, `research/README.md` | Deterministic manifest and safe Markdown export |
| Assistant stream | `.agents/skills/youtube-cumulative-research/`, packaged assistant assets, assistant setup sources, `tests/test_agent_assets.py`, `tests/test_assistant_setup.py` | Portable main-session workflow skill and safe asset-only upgrades |
| Coordinator finalization | README, roadmap, changelog, implementation status, prompt examples, end-to-end tests | Release evidence and documentation truth |

No worker other than the coordinator modifies `cli.py`, shared research models,
or shared test fixtures. If an interface must change, stop that stream and
change this plan and the spec before editing code.

## Dependency and Parallelization Graph

```text
Task 0 gates and probes ───────────────────────────────────────────────┐
Task 1 shared contracts                                               │
          ├── Task 2 store ───────┐                                   │
          ├── Task 3 assessment ──┼── Task 5 foundation CLI ─────────┤
          └── Task 4 discovery ───┘          │                        │
                                              ├── Task 6 candidates ──┤
                                              │          │             │
                                              │          └ Task 7 acquisition
                                              └── Task 8 dossier       │

Task 7 + Task 8 ── Task 9 assistants ── Task 10 final verification ───┘
```

Tasks 2, 3, and 4 run in parallel after Task 1. Task 8 may run in parallel
with Task 7 after the assessment and store schemas are stable. Phase 0 probes
run in parallel with safe foundation implementation, but automated acquisition
must remain disabled until the relevance and discovery gates pass.

---

### Task 0: Produce Phase 0 Evidence Gates

**Files:**
- Create: `plans/evidence/2026-08-31-cumulative-research-pilot-queries.json`
- Create: `plans/evidence/2026-08-31-cumulative-research-gates.json`
- Create: `plans/evidence/2026-08-31-cumulative-research-gates.md`
- Use: `scripts/prepare_search_relevance_evaluation.py`
- Use: `scripts/benchmark_search.py`

**Interfaces:**
- Consumes: the representative 50-VTT selection, the full local corpus, and the three user-approved research subjects.
- Produces: one placeholder-free pilot query file, one unreviewed twenty-result packet, three no-write discovery probe results, five refresh durations, and human-readable plus machine-readable gate reports whose unmeasured fields remain `UNKNOWN`.

- [ ] **Step 1: Write the real pilot query input**

Create the query file by copying the existing schema and replacing the four
pilot cases with these exact inputs:

```json
{
  "id": "pilot-ai-teams-01",
  "phase": "pilot",
  "subject_id": "ai-in-tech-product-teams",
  "label": "AI workflows in product and engineering teams",
  "query": "AI product engineering team workflows",
  "category": "natural_question",
  "query_language": "en",
  "filters": {"channel": null, "language": null}
}
```

```json
{
  "id": "pilot-ai-teams-02",
  "phase": "pilot",
  "subject_id": "ai-in-tech-product-teams",
  "label": "AI adoption across product development teams",
  "query": "AI adoption product development teams",
  "category": "paraphrase",
  "query_language": "en",
  "filters": {"channel": null, "language": null}
}
```

```json
{
  "id": "pilot-local-inference-01",
  "phase": "pilot",
  "subject_id": "local-ai-inference-cost",
  "label": "Cost-efficient local inference with MLX and Ollama",
  "query": "local LLM inference cost MLX Ollama",
  "category": "natural_question",
  "query_language": "en",
  "filters": {"channel": null, "language": null}
}
```

```json
{
  "id": "pilot-code-quality-01",
  "phase": "pilot",
  "subject_id": "ai-code-quality",
  "label": "AI code quality rules testing and review",
  "query": "AI code quality rules testing code review",
  "category": "natural_question",
  "query_language": "en",
  "filters": {"channel": null, "language": null}
}
```

Keep the existing `pilot` and `release` contracts unchanged. Do not invent
human judgments or release cases.

- [ ] **Step 2: Validate the query input**

Run:

```bash
python -m json.tool plans/evidence/2026-08-31-cumulative-research-pilot-queries.json >/dev/null
```

Expected: exit 0 and no value matching `REPLACE_WITH`, `TODO`, or `TBD`.

- [ ] **Step 3: Prepare the relevance packet**

Build a new representative exactly-50 VTT index in a new temporary directory,
then run the documented preparation command with `--top-k 10`. Do not reuse an
old generated packet or edit the result ordering.

Expected: four pilot queries, exactly twenty rank-1-to-5 judgments left `null`,
at least three distinct subjects, and packet status `UNKNOWN`.

- [ ] **Step 4: Run three no-write discovery probes in parallel**

For each subject, invoke yt-dlp metadata search with an exact bounded source:

```text
ytsearch10:AI workflows in product and engineering teams
ytsearch10:local LLM inference cost MLX Ollama
ytsearch10:AI code quality rules testing code review
```

Capture exit code, candidate IDs, titles, channels when available, publication
dates, errors, and before/after hashes of the local corpus and both databases.
The gate passes only if every subject returns at least five plausible distinct
videos and all local hashes remain identical.

- [ ] **Step 5: Measure five full refreshes**

Build into a fresh temporary output pair for each measurement using the current
full local corpus. Record wall time, exit code, document count, passage count,
database size, and validation result. Do not treat the historical 48.75 second
uncommitted measurement as one of the five current runs.

Expected: five validated runs. If p95 exceeds 60 seconds, set
`incremental_refresh_required=true` and expose a performance warning. This
blocks global activation, not an exact local batch approved by the user.

- [ ] **Step 6: Record the gate report**

The Markdown report contains exact commands, code SHA, corpus fingerprint,
artifact hashes, raw result paths, and these statuses:

```text
relevance_pilot: UNKNOWN until human review
discovery_probe: PASS | FAIL | UNKNOWN
refresh_performance: PASS | BLOCKED | UNKNOWN
global_activation_ready: false
```

Write the same evidence in a validated JSON artifact with schema version 1,
the measured code SHA and corpus fingerprint, the three gate statuses, the
pilot judgment counts, refresh sample count and p95, and discovery subject
counts. Reject unknown keys, invalid status values, and missing fingerprints.
This is release evidence, not a runtime authorization token: later code commits
must not make an exact user-approved local acquisition inaccessible.

- [ ] **Step 7: Commit only versionable inputs and the gate report**

```bash
git add plans/evidence/2026-08-31-cumulative-research-pilot-queries.json \
  plans/evidence/2026-08-31-cumulative-research-gates.json \
  plans/evidence/2026-08-31-cumulative-research-gates.md
git commit -m "test: prepare cumulative research gates"
```

Do not commit large SQLite databases, raw cookies, absolute private corpus
paths, or an unreviewed packet containing local path leakage.

---

### Task 1: Freeze Shared Research Models and Paths

**Files:**
- Create: `src/yt_insights/research/__init__.py`
- Create: `src/yt_insights/research/models.py`
- Modify: `src/yt_insights/paths.py`
- Modify: `src/yt_insights/config.py`
- Test: `tests/research/test_models.py`
- Modify test: `tests/test_paths.py`
- Modify test: `tests/test_config.py`

**Interfaces:**
- Consumes: only Python standard-library types.
- Produces: `FreshnessProfile`, `ResearchState`, `RequiredUserAction`, `CandidateStatus`, `QuerySpec`, `DatabaseSnapshot`, `PassageEvidence`, `VideoEvidence`, `CoverageMetrics`, `FreshnessAssessment`, `ResearchAssessment`, `ResearchCandidate`, `ResearchSession`, `DecisionRecord`, `AcquisitionAttempt`, `ResearchAcquisitionOutcome`, `EventRecord`, `SessionHistory`, `normalize_research_text()`, and `discovery_fingerprint()`.

- [ ] **Step 1: Write failing path and configuration tests**

Add assertions:

```python
paths = DataPaths.from_root(tmp_path / "corpus")
assert paths.research_database == (tmp_path / "corpus" / ".research" / "research-v1.sqlite3").resolve()

config = load_config({"data_root": tmp_path / "corpus", "research_output_root": tmp_path / "tracked"})
assert config.research_output_root == tmp_path / "tracked"
```

Also assert `YT_INSIGHTS_RESEARCH_OUTPUT_ROOT` is accepted, an omitted value
stays `None`, and the template documents the setting without exposing secrets.

- [ ] **Step 2: Run the path/config tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_paths.py tests/test_config.py -q
```

Expected: failures for missing `research_database` and
`research_output_root`.

- [ ] **Step 3: Extend paths and configuration minimally**

Add this field to `DataPaths` and derive it in `from_root`:

```python
research_database: Path

research_database=resolved / ".research" / "research-v1.sqlite3",
```

Add to `Config`:

```python
research_output_root: Path | None = None
```

Include it in path coercion and map
`YT_INSIGHTS_RESEARCH_OUTPUT_ROOT`. Do not derive it from the current working
directory.

- [ ] **Step 4: Write failing domain-model tests**

Cover exact enum values, freshness days, one-to-eight query validation,
500-code-point limits, NUL/control characters, normalized duplicates, video ID,
canonical watch URL, finite ranks, date parsing, immutable tuple fields, and
fingerprint determinism. Include this contract test:

```python
queries = (
    QuerySpec("Local LLM inference cost"),
    QuerySpec("MLX versus Ollama performance"),
)
fingerprint = discovery_fingerprint(
    topic="Local AI inference",
    queries=queries,
    languages=("en",),
    provider_name="yt-dlp",
    provider_version=1,
)
assert len(fingerprint) == 64
assert fingerprint == discovery_fingerprint(
    topic="  local   AI inference ",
    queries=queries,
    languages=("en",),
    provider_name="yt-dlp",
    provider_version=1,
)
```

- [ ] **Step 5: Run the model tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_models.py -q
```

Expected: import failure for `yt_insights.research.models`.

- [ ] **Step 6: Implement the exact shared enums and signatures**

```python
class FreshnessProfile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    STABLE = "stable"
    HISTORICAL = "historical"

    @property
    def maximum_age_days(self) -> int | None:
        return {
            self.FAST: 14,
            self.STANDARD: 30,
            self.STABLE: 90,
            self.HISTORICAL: None,
        }[self]


class ResearchState(StrEnum):
    ASSESSING = "assessing"
    AWAITING_SUFFICIENCY = "awaiting_sufficiency_confirmation"
    DISCOVERING = "discovering"
    AWAITING_CANDIDATES = "awaiting_candidate_approval"
    ACQUIRING = "acquiring"
    REINDEXING = "reindexing"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    CANCELLED = "cancelled"


class RequiredUserAction(StrEnum):
    CONFIRM_SUFFICIENCY_OR_REFRESH = "confirm_sufficiency_or_refresh"
    APPROVE_CANDIDATES_OR_CANCEL = "approve_candidates_or_cancel"


class CandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    ACQUIRED = "acquired"
    ALREADY_PRESENT = "already_present"
    NO_TRANSCRIPT = "no_transcript"
    FAILED_RETRYABLE = "failed_retryable"
```

Implement frozen, slotted dataclasses with these constructor fields:

```python
QuerySpec(text: str)
DatabaseSnapshot(search_generation: str, catalog_generation: str)
PassageEvidence(query: str, passage_id: str, video_id: str, channel_id: str, rank: int, url: str, excerpt: str, source_sha256: str)
VideoEvidence(query: str, video_id: str, source_keys: tuple[str, ...], title: str, published_at: date | None, rank: int, watch_url: str)
CoverageMetrics(matched_passages: int, matched_videos: int, distinct_channels: int, queries_with_zero_hits: tuple[str, ...], newest_source_published_at: date | None, unknown_publication_date_count: int)
FreshnessAssessment(profile: FreshnessProfile, maximum_age_days: int | None, last_successful_discovery_at: datetime | None, stale: bool, reason: str)
ResearchAssessment(created_at: datetime, snapshot: DatabaseSnapshot, coverage: CoverageMetrics, freshness: FreshnessAssessment, passages: tuple[PassageEvidence, ...], videos: tuple[VideoEvidence, ...])
ResearchCandidate(video_id: str, title: str, channel_id: str | None, channel_title: str | None, published_at: date | None, watch_url: str, matched_queries: tuple[str, ...], original_rank: int, status: CandidateStatus)
ResearchSession(session_id: str, topic: str, queries: tuple[QuerySpec, ...], languages: tuple[str, ...], freshness_profile: FreshnessProfile, discovery_fingerprint: str, state: ResearchState, required_user_action: RequiredUserAction | None, revision: int, retry_target: ResearchState | None, created_at: datetime, updated_at: datetime)
DecisionRecord(idempotency_key: str, action: str, payload_json: str, created_at: datetime)
AcquisitionAttempt(attempt_id: str, idempotency_key: str, session_id: str, revision: int, status: str, video_ids: tuple[str, ...], created_at: datetime, updated_at: datetime)
ResearchAcquisitionOutcome(attempt_id: str, video_id: str, status: CandidateStatus, error_code: str | None, source_sha256: str | None)
EventRecord(event_id: int, from_state: ResearchState | None, to_state: ResearchState, event_code: str, payload_json: str, created_at: datetime)
SessionHistory(assessments: tuple[ResearchAssessment, ...], decisions: tuple[DecisionRecord, ...], acquisition_attempts: tuple[AcquisitionAttempt, ...], acquisition_outcomes: tuple[ResearchAcquisitionOutcome, ...], events: tuple[EventRecord, ...])
```

Use canonical JSON with UTF-8 and `sort_keys=True`, then SHA-256, for the
fingerprint. Normalization is Unicode NFKC, trim, collapse whitespace, and
casefold. Preserve original display text in every model.

- [ ] **Step 7: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_models.py tests/test_paths.py tests/test_config.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/yt_insights/research/__init__.py src/yt_insights/research/models.py \
  src/yt_insights/paths.py src/yt_insights/config.py \
  tests/research/test_models.py tests/test_paths.py tests/test_config.py
git commit -m "feat(research): add shared workflow contracts"
```

---

### Task 2: Add the Persistent Research Store

**Files:**
- Create: `src/yt_insights/research/store.py`
- Create: `tests/research/test_store.py`

**Interfaces:**
- Consumes: Task 1 models and an absolute database path.
- Produces: session lifecycle, latest-assessment and history reads, candidate decisions, durable acquisition attempts and outcomes, reindexing transitions, failure recovery, retry, and cancellation.

- [ ] **Step 1: Write failing schema and lifecycle tests**

Test a new database, schema version 1, quick-check, one created session,
ordered queries, revision zero, and state `assessing`. Assert a second
`create_session` with the same explicit session ID fails without modifying the
first row.

Use a deterministic clock and session ID factory in tests:

```python
store = ResearchStore(
    tmp_path / "research.sqlite3",
    now=lambda: datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
)
session = store.create_session(
    session_id="01K4RESEARCH0000000000000000",
    topic="Local AI inference",
    queries=(QuerySpec("local LLM inference"),),
    languages=("en",),
    freshness_profile=FreshnessProfile.FAST,
    discovery_fingerprint="a" * 64,
)
assert session.revision == 0
assert session.state is ResearchState.ASSESSING
```

- [ ] **Step 2: Run the store test and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_store.py -q
```

Expected: import failure for `yt_insights.research.store`.

- [ ] **Step 3: Implement schema creation and validation**

Create exactly these tables in one transaction:

```sql
CREATE TABLE schema_meta(version INTEGER NOT NULL);
CREATE TABLE research_sessions(
  session_id TEXT PRIMARY KEY,
  topic TEXT NOT NULL,
  discovery_fingerprint TEXT NOT NULL,
  freshness_profile TEXT NOT NULL,
  state TEXT NOT NULL,
  required_user_action TEXT,
  revision INTEGER NOT NULL,
  retry_target TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE research_queries(
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  query_text TEXT NOT NULL,
  normalized_query TEXT NOT NULL,
  PRIMARY KEY(session_id, ordinal),
  UNIQUE(session_id, normalized_query)
);
CREATE TABLE research_languages(
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  ordinal INTEGER NOT NULL,
  language TEXT NOT NULL,
  PRIMARY KEY(session_id, ordinal),
  UNIQUE(session_id, language)
);
CREATE TABLE research_assessments(
  assessment_id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  session_revision INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, session_revision)
);
CREATE TABLE research_candidates(
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  snapshot_revision INTEGER NOT NULL,
  video_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(session_id, snapshot_revision, video_id)
);
CREATE TABLE research_decisions(
  idempotency_key TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  expected_revision INTEGER NOT NULL,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE research_acquisition_attempts(
  attempt_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  expected_revision INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE research_acquisition_outcomes(
  attempt_id TEXT NOT NULL REFERENCES research_acquisition_attempts(attempt_id) ON DELETE CASCADE,
  video_id TEXT NOT NULL,
  status TEXT NOT NULL,
  error_code TEXT,
  source_sha256 TEXT,
  PRIMARY KEY(attempt_id, video_id)
);
CREATE TABLE research_events(
  event_id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(session_id) ON DELETE CASCADE,
  from_state TEXT,
  to_state TEXT NOT NULL,
  event_code TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

Use `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, `BEGIN IMMEDIATE`,
canonical JSON, exact schema metadata, and `PRAGMA quick_check`. Never enable
WAL for this portable local state database.

- [ ] **Step 4: Write failing transition, conflict, and idempotency tests**

Cover:

- `assessing -> awaiting_sufficiency_confirmation` through `record_assessment`;
- `sufficient -> completed`;
- `refresh -> discovering`;
- stale expected revision rejection;
- duplicate idempotency key with identical payload returns prior result;
- duplicate key with different payload fails closed;
- acquisition attempt reservation is idempotent before network access;
- per-video outcomes and `acquiring -> reindexing` commit atomically;
- `reindexing -> assessing` is explicit and revision-checked;
- latest assessment and immutable history include decisions, attempts,
  outcomes, and events in deterministic order;
- invalid transition leaves all tables unchanged;
- `record_failure` stores a bounded code and exact retry target;
- `retry` enters only the stored target;
- `cancel` works only from a non-terminal waiting state;
- database identity replacement between calls is detected.

- [ ] **Step 5: Implement the transaction helper and public methods**

Use these exact signatures:

```python
class ResearchStore:
    def create_session(self, *, session_id: str, topic: str, queries: tuple[QuerySpec, ...], languages: tuple[str, ...], freshness_profile: FreshnessProfile, discovery_fingerprint: str) -> ResearchSession: ...
    def get_session(self, session_id: str) -> ResearchSession: ...
    def record_assessment(self, session_id: str, *, expected_revision: int, assessment: ResearchAssessment) -> ResearchSession: ...
    def get_latest_assessment(self, session_id: str) -> ResearchAssessment | None: ...
    def decide_sufficiency(self, session_id: str, *, expected_revision: int, sufficient: bool, idempotency_key: str) -> ResearchSession: ...
    def last_successful_discovery_at(self, discovery_fingerprint: str) -> datetime | None: ...
    def record_candidates(self, session_id: str, *, expected_revision: int, candidates: tuple[ResearchCandidate, ...], provider_name: str, provider_version: int, errors: tuple[str, ...]) -> ResearchSession: ...
    def list_candidates(self, session_id: str) -> tuple[ResearchCandidate, ...]: ...
    def approve_candidates(self, session_id: str, *, expected_revision: int, video_ids: tuple[str, ...], idempotency_key: str) -> ResearchSession: ...
    def start_acquisition_attempt(self, session_id: str, *, expected_revision: int, video_ids: tuple[str, ...], idempotency_key: str, attempt_id: str) -> AcquisitionAttempt: ...
    def record_acquisition_batch(self, session_id: str, *, expected_revision: int, attempt_id: str, outcomes: tuple[ResearchAcquisitionOutcome, ...]) -> ResearchSession: ...
    def complete_reindexing(self, session_id: str, *, expected_revision: int) -> ResearchSession: ...
    def get_session_history(self, session_id: str) -> SessionHistory: ...
    def record_failure(self, session_id: str, *, expected_revision: int, retry_target: ResearchState, error_code: str) -> ResearchSession: ...
    def retry(self, session_id: str, *, expected_revision: int, idempotency_key: str) -> ResearchSession: ...
    def cancel(self, session_id: str, *, expected_revision: int, idempotency_key: str) -> ResearchSession: ...
```

Bound stored error codes to 100 ASCII characters. Do not store external
exception text in events.

- [ ] **Step 6: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_store.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/yt_insights/research/store.py tests/research/test_store.py
git commit -m "feat(research): persist resumable sessions"
```

---

### Task 3: Implement Objective Local Assessment

**Files:**
- Create: `src/yt_insights/research/assessment.py`
- Create: `tests/research/test_assessment.py`

**Interfaces:**
- Consumes: Task 1 models, `SearchService`, `SQLiteFtsIndex`, and `Catalog.open_read_only`.
- Produces: `SQLiteEvidenceReader` and `assess_local()`.

- [ ] **Step 1: Write failing freshness and aggregation tests**

Use fake passage and video readers. Cover exact 14/30/90-day boundaries,
`never_checked`, `refresh_not_required`, timezone-aware UTC rejection, unique
passage/video counts across queries, unknown dates, distinct channels, and
queries with zero passage and video hits.

```python
assessment = assess_local(
    queries=(QuerySpec("local inference"),),
    profile=FreshnessProfile.FAST,
    evidence_reader=fake_reader,
    last_successful_discovery_at=datetime(2026, 8, 17, 12, tzinfo=UTC),
    now=datetime(2026, 8, 31, 12, tzinfo=UTC),
)
assert assessment.freshness.stale is False
assert assessment.coverage.matched_videos == 2
```

At exactly fourteen elapsed days, `fast` is fresh. One microsecond later it is
stale.

- [ ] **Step 2: Run assessment tests and verify RED**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_assessment.py -q
```

Expected: import failure for `yt_insights.research.assessment`.

- [ ] **Step 3: Implement the reader protocol and pure aggregation**

```python
class EvidenceReader(Protocol):
    def capture_snapshot(self) -> DatabaseSnapshot: ...
    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None: ...
    def search_passages(self, query: QuerySpec, *, languages: tuple[str, ...], limit: int) -> tuple[PassageEvidence, ...]: ...
    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]: ...


def assess_local(
    *,
    queries: tuple[QuerySpec, ...],
    profile: FreshnessProfile,
    evidence_reader: EvidenceReader,
    last_successful_discovery_at: datetime | None,
    now: datetime,
) -> ResearchAssessment: ...
```

Capture a database snapshot before the first query, call each reader with limit
20, and validate the same snapshot after the final query. Fail retryably if
either immutable database identity changed. Deduplicate passages by
`passage_id` and videos
by `video_id`, preserving the lowest rank then query order. `matched_videos` is
the union of passage and metadata video IDs. `queries_with_zero_hits` includes
a query only when both readers return no evidence.

`distinct_channels` counts only non-empty real `channel_id` values carried by
passage evidence. Catalogue source slugs such as `inbox` are provenance keys,
not YouTube channel identities, and must never increment that metric.
Publication-date metrics and metadata video counts come from one bounded
read-only catalogue snapshot. Passage searches may reopen the immutable search
database per query, so the response documents that multi-query assessment is
not a cross-database transaction snapshot.

- [ ] **Step 4: Write failing real-adapter tests**

Build a tiny temporary FTS index and catalogue with two videos and two dates.
Assert:

- search databases are opened read-only;
- returned passage IDs, exact URLs, and video dates map into evidence models;
- bounded excerpts and exact source SHA-256 values map from `SearchHit`;
- replacing either database during a multi-query assessment fails without
  returning a mixed-generation result;
- one or several language filters are handled deterministically;
- database files and SHA-256 hashes remain unchanged;
- missing or invalid database errors are bounded and do not contain query text.

- [ ] **Step 5: Implement `SQLiteEvidenceReader`**

```python
class SQLiteEvidenceReader:
    def __init__(self, *, search_database: Path, catalog_database: Path) -> None: ...
    def search_passages(self, query: QuerySpec, *, languages: tuple[str, ...], limit: int) -> tuple[PassageEvidence, ...]: ...
    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]: ...
```

Derive each opaque generation value by hashing canonical JSON containing only
device, inode, size, and nanosecond mtime captured from a securely opened
regular file. Store no absolute path. Current writers publish by replacement,
so any supported publication changes the identity. Snapshot validation is an
assessment consistency guard, not a cryptographic database-content proof.

For zero or one language, perform one search. For several languages, perform
one bounded search per language, merge by passage ID, sort by original language
order then rank, and truncate to 20. Catalogue video results use `sources[0]`
through `sources` as provenance-only `source_keys`; they never synthesize a
channel identifier from a source slug or video ID.

- [ ] **Step 6: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_assessment.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/yt_insights/research/assessment.py tests/research/test_assessment.py
git commit -m "feat(research): assess local coverage and freshness"
```

---

### Task 4: Add Bounded Topic Discovery

**Files:**
- Create: `src/yt_insights/research/discovery.py`
- Modify: `src/yt_insights/downloader.py`
- Modify: `src/yt_insights/catalog.py`
- Create: `tests/research/test_discovery.py`
- Modify test: `tests/test_downloader.py`
- Modify test: `tests/test_catalog.py`

**Interfaces:**
- Consumes: Task 1 `QuerySpec` and `ResearchCandidate`, existing `fetch_video_list()`, and a read-only catalogue.
- Produces: `DiscoveryProvider`, `YtDlpDiscoveryProvider`, `DiscoveryResult`, and `Catalog.existing_video_ids()`.

- [ ] **Step 1: Write failing downloader metadata tests**

Extend the test yt-dlp JSON record with `channel_id`, `channel`, and
`description`. Assert `VideoInfo` preserves bounded channel metadata while all
existing three-argument constructors continue to work.

- [ ] **Step 2: Extend `VideoInfo` compatibly**

```python
@dataclass
class VideoInfo:
    video_id: str
    title: str
    upload_date: str
    channel_id: str = ""
    channel_title: str = ""
```

Do not retain descriptions in the shared downloader model. Read `channel_id`
and `channel` or `uploader` only when they are bounded strings of at most 300
characters without NUL bytes.

- [ ] **Step 3: Write failing exact catalogue-membership tests**

```python
with Catalog.open_read_only(database) as catalog:
    assert catalog.existing_video_ids(("aaaaaaaaaaa", "bbbbbbbbbbb")) == frozenset({"aaaaaaaaaaa"})
```

Cover empty input, duplicates, invalid IDs, more than 100 IDs, hostile values,
and database replacement.

- [ ] **Step 4: Implement the bounded reader method**

```python
def existing_video_ids(self, video_ids: Iterable[str]) -> frozenset[str]: ...
```

Validate at most 100 distinct canonical IDs and use bound SQL placeholders.
Never concatenate IDs into SQL text.

- [ ] **Step 5: Write failing provider and diversity tests**

Use an injected fetcher and assert exact sources `ytsearch10:<query>`, no more
than eight calls, at most ten final candidates, known-ID exclusion, cross-query
deduplication, merged `matched_queries`, stable original rank, round-robin
channel diversity, partial errors, and an empty result that is not converted
to success.

- [ ] **Step 6: Implement discovery**

```python
@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    provider_name: str
    provider_version: int
    candidates: tuple[ResearchCandidate, ...]
    errors: tuple[str, ...]
    completed: bool


class DiscoveryProvider(Protocol):
    def discover(self, queries: tuple[QuerySpec, ...], *, limit: int = 10) -> DiscoveryResult: ...


class YtDlpDiscoveryProvider:
    name = "yt-dlp"
    version = 1

    def __init__(self, *, fetcher=fetch_video_list, existing_ids: Callable[[tuple[str, ...]], frozenset[str]]) -> None: ...
    def discover(self, queries: tuple[QuerySpec, ...], *, limit: int = 10) -> DiscoveryResult: ...
```

Reject a query that starts with `ytsearch`, contains NUL/control characters,
or exceeds Task 1 validation. Construct the source internally. Convert external
errors to bounded codes such as `provider_exit_nonzero`,
`invalid_metadata_record`, and `partial_metadata`; never store stderr text in
research events.

- [ ] **Step 7: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_discovery.py tests/test_downloader.py tests/test_catalog.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/yt_insights/research/discovery.py src/yt_insights/downloader.py \
  src/yt_insights/catalog.py tests/research/test_discovery.py \
  tests/test_downloader.py tests/test_catalog.py
git commit -m "feat(research): preview topic discovery candidates"
```

---

### Task 5: Expose Persistent Local Assessment Through the CLI

**Files:**
- Create: `src/yt_insights/research/workflow.py`
- Create: `src/yt_insights/cli_research.py`
- Modify: `src/yt_insights/cli.py`
- Create: `tests/research/test_workflow.py`
- Create: `tests/test_cli_research.py`

**Interfaces:**
- Consumes: Tasks 1, 2, and 3.
- Produces: `ResearchWorkflow.start()`, `status()`, `decide()`, and Click commands `research start`, `research status`, and `research decide`.

- [ ] **Step 1: Write failing workflow start tests**

Inject store, evidence reader, UTC clock, and session-ID factory. Assert start:

1. validates inputs before opening databases;
2. creates a session;
3. reads the last discovery date by exact fingerprint;
4. records one assessment;
5. returns revision 1 and `awaiting_sufficiency_confirmation`;
6. performs no network call;
7. records a retryable local-index failure without creating an assessment.

- [ ] **Step 2: Implement foundation workflow methods**

```python
class ResearchWorkflow:
    def start(self, *, topic: str, queries: tuple[str, ...], languages: tuple[str, ...], freshness_profile: FreshnessProfile) -> ResearchResponse: ...
    def status(self, session_id: str) -> ResearchResponse: ...
    def decide(self, session_id: str, *, expected_revision: int, decision: Literal["sufficient", "refresh"], idempotency_key: str) -> ResearchResponse: ...
```

`ResearchResponse.to_dict()` returns `schema_version`, `session`, latest
assessment when present, latest candidates when present, and
`required_user_action`. It never returns absolute database paths, raw exception
text, query results beyond the stored limits, or transcript bodies beyond
bounded excerpts.

For this task, `refresh` persists state `discovering` and returns
`discovery_not_configured` without calling a provider. Task 6 replaces this
temporary internal branch. The user-facing CLI must label it unavailable and
must not claim a successful refresh.

- [ ] **Step 3: Write failing CLI contract tests**

Use `CliRunner` and a temporary configured data root. Cover:

```text
research start TOPIC [--query QUERY]... [--lang LANG]... [--freshness-profile PROFILE] [--json]
research status SESSION_ID [--json]
research decide SESSION_ID sufficient --revision N --idempotency-key KEY [--json]
research decide SESSION_ID refresh --revision N --idempotency-key KEY [--json]
```

Assert JSON is stable and sorted. Human output must end with exactly:

```text
Is this evidence sufficient, or should I search YouTube for newer sources?
```

JSON must never block for input. Invalid input and database failures must not
echo untrusted query text.

- [ ] **Step 4: Implement and register the Click group**

Keep `cli.py` changes to:

```python
from .cli_research import research_group
cli.add_command(research_group)
```

The adapter loads configured data paths, opens the store and reader, delegates
to the workflow, and serializes the response. It contains no state transitions
or SQL.

- [ ] **Step 5: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_workflow.py tests/test_cli_research.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/yt_insights/research/workflow.py src/yt_insights/cli_research.py \
  src/yt_insights/cli.py tests/research/test_workflow.py tests/test_cli_research.py
git commit -m "feat(research): expose resumable local assessments"
```

---

### Task 6: Connect Discovery and Candidate Decisions

**Files:**
- Modify: `src/yt_insights/research/workflow.py`
- Modify: `src/yt_insights/cli_research.py`
- Modify: `tests/research/test_workflow.py`
- Modify: `tests/test_cli_research.py`

**Interfaces:**
- Consumes: Task 4 provider and Task 2 candidate persistence.
- Produces: `ResearchWorkflow.discover()`, `candidates()`, `approve()`, and `cancel()` plus their CLI commands.

- [ ] **Step 1: Write failing discovery workflow tests**

Assert `refresh` transitions to discovery, calls the provider once, stores the
exact snapshot, and ends in `awaiting_candidate_approval`. Assert provider
failure records `failed_retryable`, partial results remain reviewable, and no
corpus/catalog/index file changes.

- [ ] **Step 2: Implement discovery orchestration**

```python
def discover(self, session_id: str, *, expected_revision: int) -> ResearchResponse: ...
def candidates(self, session_id: str) -> ResearchResponse: ...
def approve(self, session_id: str, *, expected_revision: int, video_ids: tuple[str, ...], idempotency_key: str) -> ResearchResponse: ...
def cancel(self, session_id: str, *, expected_revision: int, idempotency_key: str) -> ResearchResponse: ...
```

`decide(..., decision="refresh")` performs only the persisted authorization and
returns state `discovering`. The separate `discover` command performs network
access. This preserves a visible boundary for host approvals.

- [ ] **Step 3: Write failing CLI tests**

Add:

```text
research discover SESSION_ID --revision N [--json]
research candidates SESSION_ID [--json]
research approve SESSION_ID VIDEO_ID... --revision N --idempotency-key KEY [--json]
research cancel SESSION_ID --revision N --idempotency-key KEY [--json]
```

Assert approve accepts one to five IDs, rejects unknown/stale IDs, and human
candidate output includes date, channel, title, URL, and matching query without
external descriptions.

- [ ] **Step 4: Implement CLI adapters and run tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_workflow.py tests/test_cli_research.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add src/yt_insights/research/workflow.py src/yt_insights/cli_research.py \
  tests/research/test_workflow.py tests/test_cli_research.py
git commit -m "feat(research): add candidate discovery approval"
```

---

### Task 7: Acquire Approved Videos and Reassess

**Files:**
- Create: `src/yt_insights/research/acquisition.py`
- Modify: `src/yt_insights/acquisition.py`
- Modify: `src/yt_insights/research/workflow.py`
- Modify: `src/yt_insights/cli_research.py`
- Create: `tests/research/test_research_acquisition.py`
- Modify: `tests/research/test_workflow.py`
- Modify: `tests/research/test_cli_research.py`
- Modify: `tests/test_cli_acquire.py`
- Modify: `tests/test_acquisition.py`

**Interfaces:**
- Consumes: approved candidates, existing `build_acquisition_plan()` and `execute_acquisition()`.
- Produces: structured per-video acquisition outcomes, public `rebuild_and_publish_indexes(data_paths: DataPaths)`, `ResearchAcquisitionService.acquire_approved()`, and `research acquire`.

- [ ] **Step 1: Write failing one-refresh-per-batch tests**

First extend the existing acquisition contract with backward-compatible,
structured per-video results:

```python
class AcquisitionItemStatus(StrEnum):
    ACQUIRED = "acquired"
    ALREADY_PRESENT = "already_present"
    NO_TRANSCRIPT = "no_transcript"
    FAILED_RETRYABLE = "failed_retryable"


@dataclass(frozen=True, slots=True)
class AcquisitionItemReport:
    video_id: str
    status: AcquisitionItemStatus
    error_code: str | None = None
    source_sha256: str | None = None
```

Add `items: tuple[AcquisitionItemReport, ...]` to `AcquisitionReport` while
preserving existing counters and serialized fields. Test exact classification:
present before the run is `already_present`; newly ready is `acquired`; a
zero-exit result without a transcript or diagnostic is `no_transcript`; a
nonzero exit or bounded downloader error is `failed_retryable`.

Then use three approved candidates with outcomes success, no transcript, and
retryable failure. Assert each exact watch URL gets one single-video plan,
`analyze=False`, `refresh_indexes=False`, and the public refresh function runs
once only when at least one transcript is ready.

- [ ] **Step 2: Expose index refresh without weakening safety**

Refactor the private function to:

```python
@dataclass(frozen=True, slots=True)
class IndexRefreshReport:
    catalog_published: bool
    search_published: bool


def rebuild_and_publish_indexes(data_paths: DataPaths) -> IndexRefreshReport:
    """Build both SQLite databases privately and publish validated replacements."""
```

Replace `plan.data_paths` with the explicit argument and keep every existing
directory confinement, lock, validation, receipt, and rollback check. Preserve
the public `execute_acquisition(..., refresh_indexes: bool = True)` parameter
for compatibility and call `rebuild_and_publish_indexes(plan.data_paths)` when
it is true. Existing acquisition tests must remain green before workflow
integration.

Strengthen pair publication: if search-index publication or its validation
fails after catalogue publication, restore both the previous catalogue and
previous search database, validate the restored pair, then raise a bounded
error. If no previous pair existed, remove the partially published new member
and validate that neither active database remains. A new catalogue must never
remain paired with an old or missing search index.

- [ ] **Step 3: Implement the research acquisition adapter**

```python
@dataclass(frozen=True, slots=True)
class CandidateAcquisitionOutcome:
    video_id: str
    status: CandidateStatus
    error_code: str | None
    source_sha256: str | None


class ResearchAcquisitionService:
    def acquire_approved(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        data_paths: DataPaths,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> tuple[CandidateAcquisitionOutcome, ...]: ...
```

Map the new `AcquisitionReport.items` directly to research candidate outcomes.
Never infer a per-video status from aggregate counters or free-form failure
strings. Never substitute a related video. Do not call an LLM.

- [ ] **Step 4: Write failing workflow recovery tests**

Cover:

- only latest approved IDs are acquired;
- stale approval revision fails before writes;
- an idempotent acquisition attempt is reserved before network access;
- replaying the same attempt key and payload returns the prior attempt;
- reusing the key with a different payload fails before writes;
- outcomes and `acquiring -> reindexing` persist in one transaction;
- index refresh failure keeps acquired files and records retry target
  `reindexing`;
- retry refresh does not redownload successful videos;
- valid refresh triggers a new assessment and the mandatory sufficiency state;
- repeated acquire attempt is idempotent and does not replace immutable files.

- [ ] **Step 5: Implement `research acquire` and retry**

```text
research acquire SESSION_ID --revision N --idempotency-key KEY [--lang LANG] [--cookies-from-browser BROWSER] [--json]
research retry SESSION_ID --revision N --idempotency-key KEY [--json]
```

The command reports Task 0 gate statuses as bounded warnings when the evidence
artifact is available. `UNKNOWN`, `FAIL`, an old measured code SHA, or an absent
artifact blocks global activation claims but not an exact one-to-five-ID local
batch that the user approved at the current session revision.

- [ ] **Step 6: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_research_acquisition.py \
  tests/research/test_workflow.py tests/research/test_cli_research.py \
  tests/test_cli_acquire.py tests/test_acquisition.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 7: Commit**

```bash
git add src/yt_insights/research/acquisition.py src/yt_insights/acquisition.py \
  src/yt_insights/research/workflow.py src/yt_insights/cli_research.py \
  tests/research/test_research_acquisition.py tests/research/test_workflow.py \
  tests/research/test_cli_research.py tests/test_cli_acquire.py \
  tests/test_acquisition.py
git commit -m "feat(research): acquire approved sources and reassess"
```

---

### Task 8: Export a Versioned Evidence Dossier

**Files:**
- Create: `src/yt_insights/research/dossier.py`
- Create: `tests/research/test_dossier.py`
- Create: `research/README.md`
- Modify: `src/yt_insights/research/workflow.py`
- Modify: `src/yt_insights/cli_research.py`
- Modify: `tests/test_cli_research.py`

**Interfaces:**
- Consumes: completed or waiting session, assessments, decisions, candidates, and source hashes.
- Produces: `DossierExportRequest`, `DossierExportResult`, `export_dossier()`, and `research export`.

- [ ] **Step 1: Write failing deterministic manifest tests**

Freeze a session fixture and assert two exports in separate empty directories
produce byte-identical `manifest.json` and `dossier.md`. Assert the manifest has
no absolute paths, secrets, SQL, transcript bodies beyond selected evidence,
or unstable current timestamps.

- [ ] **Step 2: Write failing path-safety tests**

Cover existing target without `force`, symlink target, non-directory parent,
path traversal, destination swap, interrupted publication, and explicit safe
copy into a different project root.

- [ ] **Step 3: Implement request, result, and renderer**

```python
@dataclass(frozen=True, slots=True)
class DossierExportRequest:
    session_id: str
    output_directory: Path
    force: bool = False


@dataclass(frozen=True, slots=True)
class DossierExportResult:
    directory: Path
    manifest_sha256: str
    dossier_sha256: str

    def to_dict(self) -> dict[str, str]: ...


def export_dossier(request: DossierExportRequest, *, store: ResearchStore, package_version: str) -> DossierExportResult: ...
```

The exporter reads only `get_session()`, `get_latest_assessment()`,
`list_candidates()`, and `get_session_history()`. Passage excerpts and source
hashes come from the stored assessment; acquired hashes and failures come from
the stored acquisition outcomes. Missing expected evidence remains an explicit
coverage limit and is never synthesized.

Stage both files in a private sibling directory, fsync them, validate JSON and
hashes, then publish the directory. `--force` replaces only a validated prior
dossier directory with exactly the two expected regular files.

The Markdown sections are exactly:

```text
# <topic>
## Research scope
## Freshness and coverage
## Source-backed evidence
## Newly acquired sources
## Contradictions
## Coverage limits
## Unresolved questions
```

Do not generate an article draft or model synthesis in this deterministic
exporter.

- [ ] **Step 4: Add CLI export**

```text
research export SESSION_ID [--output DIRECTORY] [--force] [--json]
```

If output is omitted, require configured `research_output_root` and derive
`<slug>/<YYYY-MM-DD>-<session-id>`. Never use the current working directory as
an implicit root.

- [ ] **Step 5: Run focused and complete tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/research/test_dossier.py tests/test_cli_research.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

- [ ] **Step 6: Commit**

```bash
git add src/yt_insights/research/dossier.py src/yt_insights/research/workflow.py \
  src/yt_insights/cli_research.py tests/research/test_dossier.py \
  tests/test_cli_research.py research/README.md
git commit -m "feat(research): export versioned evidence dossiers"
```

---

### Task 9: Add the Claude Code and Codex Workflow Skill

**Files:**
- Create: `.agents/skills/youtube-cumulative-research/SKILL.md`
- Create: `.agents/skills/youtube-cumulative-research/agents/openai.yaml`
- Create: `src/yt_insights/assistant_assets/skills/youtube-cumulative-research/SKILL.md`
- Create: `src/yt_insights/assistant_assets/skills/youtube-cumulative-research/agents/openai.yaml`
- Modify: `src/yt_insights/assistant_setup.py`
- Modify: `tests/test_agent_assets.py`
- Modify: `tests/test_assistant_setup.py`
- Modify: `tests/fixtures/agent-routing.json`
- Modify: `tests/test_agent_routing_fixture.py`
- Modify: `scripts/check_agent_routing_fixture.py`
- Modify: `tests/test_cli_agent_commands.py`
- Modify: `tests/test_packaging.py`
- Modify: `scripts/smoke_wheel.py`
- Modify: `examples/agent-prompts.md`

**Interfaces:**
- Consumes: stable Task 5 through Task 8 CLI JSON schema.
- Produces: one portable main-session skill discoverable by fresh Claude Code and Codex sessions. No new writable native subagent is added.

- [ ] **Step 1: Write failing asset and setup tests**

Assert:

- the new skill is included in packaged and shared setup manifests;
- existing `youtube-research` and native researcher remain read-only;
- the new skill never invokes the read-only MCP for mutation;
- every `awaiting_*` state maps to a user question;
- `refresh` does not imply candidate approval;
- candidate IDs are repeated exactly in the approval command;
- prompts and metadata are English;
- setup rollback and conflict detection include the new skill.
- `setup-assistants --assets-only` installs or upgrades repository skills and
  agents without reading, overwriting, or requiring ownership of an existing
  MCP registration.

- [ ] **Step 2: Write the English skill contract**

The skill must instruct the assistant to:

```text
1. Start or resume a yt-insights research session through the packaged CLI.
2. Present coverage, freshness profile, newest relevant source date, and last successful discovery date.
3. Ask whether the evidence is sufficient every time the CLI returns confirm_sufficiency_or_refresh.
4. If the user requests a refresh, run discovery only and present at most ten candidates.
5. Never approve candidates on the user's behalf.
6. Acquire only the exact approved IDs, then present the new assessment and ask again.
7. After sufficiency is confirmed, ask whether the user wants a dossier, an article draft, a corpus export, both, or nothing else.
8. Keep generated synthesis separate from YouTube source evidence.
```

It must also define fail-closed behavior for invalid JSON, missing session,
stale revision, failed relevance gate, and unavailable network.

- [ ] **Step 3: Update setup sources without changing global live files**

Add the fourth skill to repository-local candidate generation, packaged assets,
routing fixtures, wheel smoke coverage, and validation. Add an explicit
`--assets-only` setup mode so an existing MCP registration does not block a
skills-only upgrade. The default mode keeps its current MCP conflict behavior;
the new mode must not inspect or mutate MCP files and must preserve atomic
rollback for assistant assets.

Do not install it globally in this task. Global installation requires a new
inert candidate, exact redacted diff, digest approval, preimage recheck,
rollback, and fresh-session canaries.

- [ ] **Step 4: Add ready-to-copy English prompts**

Include exact examples for the three pilot subjects, resume by session ID,
refresh selection, dossier export, and current-project copy. Each prompt must
request source timestamps and explicit coverage limits.

- [ ] **Step 5: Run skill and setup tests**

```bash
PYTHONPATH=src .venv/bin/pytest tests/test_agent_assets.py \
  tests/test_assistant_setup.py tests/test_agent_routing_fixture.py \
  tests/test_cli_agent_commands.py tests/test_packaging.py -q
PYTHONPATH=src .venv/bin/pytest -q
```

Run the repository's skill validator against the new skill directory.

- [ ] **Step 6: Commit**

Use explicit paths returned by `git status --short`; do not stage generated
global candidates or unrelated existing assistant files.

```bash
git commit -m "feat: add cumulative YouTube research skill"
```

---

### Task 10: End-to-End Verification, Documentation, and Release Gate

**Files:**
- Create: `tests/research/test_end_to_end.py`
- Modify: `README.md`
- Modify: `ROADMAP.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/IMPLEMENTATION-STATUS.md`
- Modify: `docs/claude-code.md`
- Modify: `INSTALL.md`
- Modify: `llms.txt`
- Modify: `plans/specs/AGENT-PLATFORM.md`
- Modify: `plans/README.md`
- Modify: `plans/PARALLEL-SESSIONS.md`
- Modify: `docs/assets/yt-insights-workflow.*` only if the existing image source can be regenerated reproducibly

**Interfaces:**
- Consumes: all previous tasks and Phase 0 evidence.
- Produces: one replayable end-to-end proof, reconciled documentation, and explicit `PASS`, `FAIL`, or `UNKNOWN` status for each external gate.

- [ ] **Step 1: Write the failing end-to-end scenario**

Use temporary databases and fake discovery/acquisition adapters to replay:

```text
start -> local assessment -> refresh decision -> discover
-> approve two exact IDs -> acquire one and mark one no_transcript
-> refresh indexes -> reassess -> sufficient -> export dossier
```

Assert every state, revision, idempotency key, candidate status, required user
action, timestamped evidence URL, manifest hash, and no source/dossier mixing.

- [ ] **Step 2: Add hostile and negative-boundary scenarios**

Cover empty corpus, stale database replacement, invalid provider metadata,
candidate snapshot races, concurrent decisions, refresh rollback, dossier path
swap, secret-bearing provider errors, repeated exports, and zero candidates.

- [ ] **Step 3: Run quality gates**

```bash
PYTHONPATH=src .venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src
git diff --check
```

Run package/wheel smoke tests and the existing MCP smoke. Record exact counts,
commands, exit codes, and commit SHA. A structural validator does not prove
Claude or Codex runtime behavior.

- [ ] **Step 4: Reconcile documentation to observed behavior**

Document:

- local-first assessment and mandatory questions;
- deterministic freshness profiles;
- separate research and source databases;
- discovery and approval boundaries;
- exact limits of ten candidates and five acquisitions;
- dossier location and current-project export;
- current relevance, discovery, refresh, Claude, and Codex gate statuses;
- installation and rollback commands;
- features explicitly deferred by the spec.

Do not mark automatic acquisition, writable MCP, web UI, extension, vector
search, or graph storage as implemented.

Inspect the primary checkout's user-owned `CLAUDE.md` change before final
integration. Update that file only when a three-way diff proves the changes do
not overlap. If they overlap, preserve the user file and record its
documentation reconciliation as a separate follow-up instead of overwriting
it.

- [ ] **Step 5: Run fresh-client canaries**

Build an inert repository-local assistant candidate. In fresh scratch sessions,
verify Claude Code and Codex both discover the new skill and stop at the same
mandatory confirmation states. If a client cannot be run, record `UNKNOWN`, not
`PASS`.

- [ ] **Step 6: Review and commit final integration**

Request code review against the spec, fix all blockers and major findings, rerun
the complete gates, then commit only the reconciled files:

```bash
git commit -m "docs: release cumulative research workflow"
```

- [ ] **Step 7: Merge and push only after all local gates**

Verify the primary checkout has not changed since worktree creation. Merge the
isolated branch without staging the primary checkout's unrelated files. Push
`main`, report the final commit, remote, branch, test counts, and every remaining
`UNKNOWN` external gate.

---

## Definition of Done

- [ ] Phase 0 query and gate artifacts are replayable and SHA-bound.
- [ ] Pilot relevance, discovery, and performance gates are recorded as `PASS`, `FAIL`, or `UNKNOWN`; unresolved gates block global activation claims, not exact user-approved local acquisition.
- [ ] Assessment, store, and discovery streams pass independently.
- [ ] Every research cycle asks the user whether evidence is sufficient.
- [ ] Discovery and acquisition require two separate persisted decisions.
- [ ] Only exact approved IDs are acquired, at most five per cycle.
- [ ] Partial failure and stale revision scenarios are recoverable and tested.
- [ ] Dossiers are deterministic, versioned, safe, and excluded from source search.
- [ ] Existing MCP and native researcher remain read-only.
- [ ] Repository-local Claude Code and Codex skill assets pass static validation.
- [ ] Fresh-client runtime status is recorded as `PASS`, `FAIL`, or `UNKNOWN`.
- [ ] Full tests, Ruff, mypy, packaging, and diff checks pass at the final SHA.
- [ ] Main is merged and pushed only after unrelated primary-checkout changes are rechecked and preserved.
