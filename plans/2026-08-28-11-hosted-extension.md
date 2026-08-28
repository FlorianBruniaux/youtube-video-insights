# Hosted Service and YouTube Extension Implementation Plan

> **Status:** Conditional service plan. H0 may measure local usage before the gate; H1 to H7 stay closed until the activation gate is met.

**Goal:** Make yt-insights reachable from a browser and a remote Claude Code or Codex session without weakening the local-first workflow.

**Architecture:** Keep the current CLI and corpus contracts as the domain layer. Add a thin authenticated API and a worker only after local agent integration is proven. Start single-user with the existing filesystem corpus and SQLite on one persistent volume. Add object storage and PostgreSQL only when concurrent or multi-user access is required. Do not introduce a graph database until named multi-hop questions fail with passage search and filters.

**Activation gate:** Start H1 only if at least one signal is evidenced by H0 or an equivalent manual record:

- at least 10 manual YouTube sends per week for two consecutive weeks;
- regular use from a second machine;
- a real need to share one corpus or export with another person;
- repeated browser-to-terminal friction recorded on at least five tasks.

## Global constraints

- The browser extension sends a YouTube URL and user intent. It never scrapes captions itself.
- The service accepts only allowlisted YouTube URL forms and canonical video, playlist or channel identifiers.
- No browser cookies, YouTube cookies or local agent credentials are uploaded.
- Single-video acquisition may start immediately. Channel and playlist acquisition require a server-side preview plus explicit confirmation.
- Raw VTT files and generated exports remain traceable to source URLs and timestamps.
- Remote MCP access is read-only. Acquisition and export remain explicit API or CLI operations.
- Every hosted phase has a deletion, export and rollback path.
- The single-user SQLite phase has exactly one state-changing worker per data root.

### Task H0: Measure local usage without starting the hosted service

**Files:**

- Create: `src/yt_insights/usage_events.py`
- Create: `tests/test_usage_events.py`
- Modify: `src/yt_insights/cli.py`
- Modify: `docs/OPERATIONS.md`

- [ ] Record opt-in local counters for acquisition, search and export without query text, URLs or transcript content.
- [ ] Add `yt-insights usage report --json` with weekly counts and browser-friction notes.
- [ ] Keep telemetry local and disabled by default.
- [ ] Store the activation evidence in `plans/evidence/` before starting H1.

**Acceptance:** The report proves or rejects the activation gate without transmitting private content.

### Task H1: Define the hosted boundary and threat model

**Files:**

- Create: `docs/HOSTED-ARCHITECTURE.md`
- Create: `docs/THREAT-MODEL.md`
- Create: `src/yt_insights/contracts/hosted.py`
- Create: `src/yt_insights/storage.py`
- Create: `src/yt_insights/write_coordinator.py`
- Create: `tests/test_hosted_contracts.py`

- [ ] Define `Workspace`, `SourceRequest`, `AcquisitionPreview`, `Job`, `Corpus`, `Export` and `DeletionPlan` contracts.
- [ ] Define tenancy, retention, deletion, quotas, audit events and failure states.
- [ ] Freeze a filesystem-first `CorpusStorage` protocol and a single-writer coordination contract before API or worker code starts.
- [ ] Threat-model SSRF, forged YouTube identifiers, oversized playlists, credential leakage, cross-workspace reads and malicious transcript content.
- [ ] Decide the deployment target only after measuring expected storage, runtime and concurrency.

**Acceptance:** A hostile URL cannot make the worker fetch a non-YouTube host, every stored artifact belongs to exactly one workspace, concurrent mutation attempts serialize or fail closed, and deletion has a preview plus confirmation contract.

### Task H2: Ship a single-user authenticated API

**Files:**

- Create: `src/yt_insights/api/app.py`
- Create: `src/yt_insights/api/auth.py`
- Create: `src/yt_insights/api/routes_sources.py`
- Create: `src/yt_insights/api/routes_search.py`
- Create: `src/yt_insights/api/routes_exports.py`
- Create: `src/yt_insights/api/routes_data.py`
- Create: `tests/api/`
- Modify: `pyproject.toml`

- [ ] Expose preview, confirm, job status, corpus list, search and export endpoints.
- [ ] Call the same application services as the CLI. Do not shell out to duplicated scripts.
- [ ] Use one persistent data volume for the existing SQLite databases, VTT files and exports. Do not add object storage in the single-user phase.
- [ ] Add idempotency keys, bounded request sizes, per-user rate limits and structured audit logs.
- [ ] Expose corpus export plus deletion preview and confirmation. Rebuild derived indexes after source deletion and retain only the audit tombstone required by the retention policy.
- [ ] Add OpenAPI examples without secrets or private URLs.

**Acceptance:** One authenticated user can submit a video, confirm a channel preview, search the resulting corpus, download a complete export and delete a corpus without orphaned source or derived files. Replaying the same idempotency key creates no duplicate job.

### Task H3: Add a durable acquisition worker

**Files:**

- Create: `src/yt_insights/worker.py`
- Create: `src/yt_insights/jobs.py`
- Create: `tests/test_worker.py`
- Create: `tests/test_jobs.py`

- [ ] Persist job state before work starts.
- [ ] Make acquisition, indexing and export steps idempotent and resumable.
- [ ] Run exactly one state-changing job per data root. Reads may continue against the last atomically published search index.
- [ ] Bound download concurrency inside that worker, but serialize index publication, export registration and deletion.
- [ ] Add retry classes for transient network errors and permanent source errors.
- [ ] Never retry authorization, invalid URL or quota failures.

**Acceptance:** A worker interruption after each durable step resumes without duplicate VTT files, passages or exports, and two simultaneous mutation requests never write SQLite concurrently.

### Task H4: Build the minimal YouTube extension

**Files:**

- Create: `extension/manifest.json`
- Create: `extension/src/background.ts`
- Create: `extension/src/popup.tsx`
- Create: `extension/src/options.tsx`
- Create: `extension/tests/`

- [ ] Add one action: `Envoyer vers yt-insights`.
- [ ] Detect the current video, playlist or channel URL and show the normalized source type.
- [ ] Let the user choose `transcrire`, `ajouter au corpus` or `préparer un export`.
- [ ] Show the channel or playlist preview and require confirmation before starting acquisition.
- [ ] Store only a short-lived service token using the browser credential storage API.
- [ ] Request permissions for YouTube pages and the configured service origin only.

**Acceptance:** The extension sends the canonical URL and intent, never page HTML, cookies or captions. A channel cannot start before the preview is confirmed.

### Task H5: Expose remote read-only MCP

**Files:**

- Create: `src/yt_insights/mcp_remote.py`
- Create: `tests/test_mcp_remote.py`
- Modify: `docs/AGENT-INTEGRATION.md`

- [ ] Reuse the four local MCP tool contracts: `list_corpora`, `search_videos`, `search_passages`, `get_passage`.
- [ ] Authenticate each request and bind it to one workspace.
- [ ] Add rate limits, audit identifiers and result caps.
- [ ] Return stable source identifiers and YouTube timestamps.
- [ ] Do not expose acquisition, deletion, arbitrary file reads or raw SQL through MCP.

**Acceptance:** Claude Code and Codex return the same ordered passage IDs for five fixed queries against the same hosted workspace. Cross-workspace probes fail closed.

### Task H6: Add multi-user storage only when concurrency requires it

**Files:**

- Create: `migrations/`
- Create: `src/yt_insights/storage/postgres.py`
- Create: `tests/integration/test_postgres_storage.py`
- Modify: `docs/HOSTED-ARCHITECTURE.md`

Start this task only if a second user is onboarded or concurrent writes are observed.

- [ ] Store users, workspaces, sources, jobs, metadata and audit events in PostgreSQL.
- [ ] Store VTT and exports in object storage with immutable content hashes.
- [ ] Implement the object-store adapter behind the H1 `CorpusStorage` protocol and migrate one workspace with count and hash reconciliation.
- [ ] Use PostgreSQL full-text search first. Preserve the passage and result contracts.
- [ ] Benchmark migration against the current full corpus before changing the default search engine.
- [ ] Keep a reversible export back to the local filesystem and SQLite format.

**Acceptance:** Tenant isolation tests pass, p95 search remains within the agreed budget, and a workspace can be exported back to a complete local corpus.

### Task H7: Decide on embeddings or a graph from failures, not preference

**Files:**

- Create only after evidence: `plans/evidence/hosted-retrieval-failures.md`

- [ ] Collect named failed questions with expected passages or relationships.
- [ ] Add embeddings only for measured synonym or paraphrase misses.
- [ ] Add graph projections only for repeated entity and multi-hop questions that passage search cannot answer.
- [ ] Keep PostgreSQL as the source of truth. Treat vector or graph stores as rebuildable indexes.

**Acceptance:** A benchmark shows a material gain on recorded failures without regressing source traceability or tenant isolation.

## Hosted acceptance gate

- The local CLI remains fully usable without the hosted service.
- Extension requests are canonical, minimal and explicitly confirmed for bulk acquisition.
- The API and worker reuse domain contracts instead of duplicating acquisition logic.
- Remote MCP is read-only and workspace-scoped.
- No cookie or agent credential crosses the service boundary.
- Every corpus has export and deletion procedures.
- SQLite remains the first hosted store until concurrency or multi-user evidence requires PostgreSQL.
- Embeddings and graph storage remain conditional on retrieval failures.

## Effort and order

| Lot | Effort indicatif | Parallelism |
|---|---:|---|
| H0 usage evidence | 0.5 to 1 day | After local runtime telemetry contract |
| H1 boundary and threat model | 1 to 2 days | Can run beside extension wireframes |
| H2 API | 3 to 5 days | After H1 |
| H3 worker | 3 to 5 days | In parallel with H2 after contracts freeze |
| H4 extension | 2 to 4 days | In parallel with H2/H3 after API schema freezes |
| H5 remote MCP | 2 to 3 days | After H2 auth and search endpoints |
| H6 PostgreSQL migration | 4 to 7 days | Conditional, after measured concurrency |
| H7 embeddings or graph | Unknown until a failure corpus exists | Conditional |
