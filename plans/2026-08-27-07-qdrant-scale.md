# Conditional Qdrant Scale-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move an already validated hybrid retriever to Qdrant only when operational scale requires a remote vector service.

**Architecture:** Qdrant implements the existing dense-retriever port; lexical FTS5 remains available for local fallback and parity checks. Every migration builds an immutable collection, validates it, snapshots it, and swaps a stable alias atomically.

**Tech Stack:** Python 3.11+, optional qdrant-client, Qdrant with TLS/API key, existing search/evaluation contracts, pytest

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Do not begin implementation unless the hybrid decision is `ADOPT` and at least one Qdrant operational trigger is measured.
- If either prerequisite is absent, close the issue as `wontfix` for the current release rather than adding dormant infrastructure.
- Never delete or recreate the active collection in place.
- Never store an API key in source, workflow YAML, logs, or browser code.
- Collection identity includes schema, embedding model revision, dimension, and distance metric.
- A snapshot restore and alias rollback are mandatory before production cutover.

---

### Task 1: Record the scale-out gate

**Files:**
- Create: `plans/decisions/QDRANT-SCALE-DECISION.md`
- Read: `plans/decisions/HYBRID-SEARCH-DECISION.md`
- Read: latest ignored benchmark reports under `output/.search/benchmarks/`

**Interfaces:**
- Consumes: measured relevance, passage count, index size, concurrency, service requirements, and expected monthly cost
- Produces: `ADOPT`, `REJECT`, or `UNKNOWN`

- [ ] **Step 1: Verify the hybrid prerequisite**

The hybrid decision must be `ADOPT` with its dataset and report fingerprints. Any other value makes this scale-out decision `REJECT`.

- [ ] **Step 2: Verify at least one operational trigger**

Record concrete evidence for one or more:

- approximately one million passages;
- multi-gigabyte vector index;
- remote or multi-user service requirement;
- measured concurrency exceeding the SQLite SLA;
- replication, tenant-filtering, or independent availability requirement.

- [ ] **Step 3: Record operating targets**

Set expected query volume, p95 latency, storage, monthly cost ceiling, RPO, and RTO. Initial maximum recovery targets are RPO 24 hours and RTO 1 hour unless a stricter product requirement is documented.

- [ ] **Step 4: Stop or authorize implementation**

Only `ADOPT` authorizes Task 2. Commit the decision independently:

```bash
rtk git add plans/decisions/QDRANT-SCALE-DECISION.md
rtk git commit -m "docs(search): record Qdrant scale decision"
```

### Task 2: Add the optional adapter boundary

**Files:**
- Modify: `pyproject.toml`
- Create: `src/yt_insights/search/qdrant_index.py`
- Test: `tests/search/test_qdrant_index.py`

**Interfaces:**
- Consumes: validated vector cache and `SearchQuery` filters
- Produces: `QdrantDenseRetriever` implementing the existing dense-retriever protocol

- [ ] **Step 1: Write failing adapter tests with a fake client**

Verify query vector, limit, channel/language/date filters, passage identity, timeouts, and error translation. Assert payload text is not trusted as HTML and unknown payload fields are ignored rather than executed.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_qdrant_index.py -q
```

Expected: adapter is absent.

- [ ] **Step 3: Add an isolated optional dependency**

Add `qdrant = ["qdrant-client>=1.9"]`. Core and semantic installations must not import qdrant-client.

- [ ] **Step 4: Implement explicit configuration**

Read URL, API key, timeout, and alias from environment/config. Reject non-HTTPS remote URLs; allow plain HTTP only for loopback integration tests. Redact API keys from exceptions and repr output.

- [ ] **Step 5: Implement payload mapping**

Store `passage_id`, `document_id`, `source_type`, `media_id`, `channel`, `language`, `upload_date`, `kind`, timestamps, source hash, text hash, and corpus-relative path. Create payload indexes for channel, language, date, source type, and kind before backfill.

- [ ] **Step 6: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_qdrant_index.py -q
rtk .venv/bin/pytest -q
rtk git add pyproject.toml src/yt_insights/search/qdrant_index.py tests/search/test_qdrant_index.py
rtk git commit -m "feat(search): add optional Qdrant retriever"
```

### Task 3: Implement immutable collection lifecycle

**Files:**
- Create: `src/yt_insights/search/qdrant_migration.py`
- Test: `tests/search/test_qdrant_migration.py`

**Interfaces:**
- Consumes: model metadata, schema version, vector dimension, distance, alias
- Produces: new collection name `yt-insights-passages-v<schema>-<modelhash>-<timestamp>` and atomic alias swap

- [ ] **Step 1: Write destructive-boundary tests**

Use a fake client and assert no code path calls delete on the collection currently targeted by the active alias. Assert schema or dimension changes create a new collection name.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_qdrant_migration.py -q
```

Expected: migration module is absent.

- [ ] **Step 3: Implement create-and-validate lifecycle**

Create the new collection, create payload indexes, batch-upsert vectors with deterministic UUIDs derived from passage IDs, then verify collection configuration and point counts before any alias operation.

- [ ] **Step 4: Implement snapshot-before-cutover**

Request a collection snapshot after validation and record its identifier. Refuse alias cutover if snapshot creation or verification fails.

- [ ] **Step 5: Implement one atomic alias operation**

Use one Qdrant alias update request that removes the alias from the old collection and assigns it to the new collection. Preserve the old collection throughout the rollback window.

- [ ] **Step 6: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_qdrant_migration.py -q
rtk git add src/yt_insights/search/qdrant_migration.py tests/search/test_qdrant_migration.py
rtk git commit -m "feat(search): migrate Qdrant collections safely"
```

### Task 4: Reconcile backfill integrity and relevance parity

**Files:**
- Create: `scripts/reconcile_qdrant.py`
- Test: `tests/search/test_reconcile_qdrant.py`

**Interfaces:**
- Consumes: local manifest/vector cache and a candidate Qdrant collection
- Produces: exact count/hash reconciliation plus retrieval parity report

- [ ] **Step 1: Write failing reconciliation tests**

Detect missing IDs, unexpected IDs, duplicate deterministic IDs, text-hash mismatch, vector-config mismatch, payload-filter mismatch, and count mismatch.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_reconcile_qdrant.py -q
```

Expected: script is absent.

- [ ] **Step 3: Implement paginated reconciliation**

Scroll every candidate point with explicit pagination. Compare sets and hashes against the local manifest; sampling is insufficient for the integrity gate.

- [ ] **Step 4: Run the held-out evaluation against the candidate**

Use the same dataset fingerprint and RRF configuration as the adopted local hybrid system. Candidate nDCG@10 and Recall@5 must not regress beyond 0.5 points absolute.

- [ ] **Step 5: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_reconcile_qdrant.py -q
rtk git add scripts/reconcile_qdrant.py tests/search/test_reconcile_qdrant.py
rtk git commit -m "feat(search): reconcile Qdrant backfills"
```

### Task 5: Test cutover, rollback, and recovery

**Files:**
- Create: `runbook/qdrant-migration.md`
- Create: `scripts/smoke_qdrant.py`
- Test: `tests/search/test_smoke_qdrant.py`

**Interfaces:**
- Consumes: candidate collection, active alias, snapshot ID, smoke query IDs
- Produces: repeatable cutover, rollback, and snapshot-restore evidence

- [ ] **Step 1: Document exact preflight gates**

Require TLS/API-key health, candidate reconciliation, relevance parity, snapshot ID, old collection name, rollback command, disk capacity, and operator identity before cutover.

- [ ] **Step 2: Implement smoke checks**

Check health, alias target, point count, one filtered query per indexed field, ten frozen relevance queries, latency, and absence of API keys in output.

- [ ] **Step 3: Execute a non-production alias swap and rollback**

Measure time from alias change to successful smoke queries, then swap back to the old collection and repeat. Target rollback is under five minutes.

- [ ] **Step 4: Restore the snapshot in an isolated collection**

Verify restored point count, payload indexes, hashes, and smoke queries. Record achieved RTO and compare it with the decision target.

- [ ] **Step 5: Run complete verification**

```bash
rtk .venv/bin/pytest tests/search/test_qdrant_index.py tests/search/test_qdrant_migration.py tests/search/test_reconcile_qdrant.py tests/search/test_smoke_qdrant.py -q
rtk .venv/bin/pytest -q
rtk git diff --check
```

- [ ] **Step 6: Commit the operational slice**

```bash
rtk git add runbook/qdrant-migration.md scripts/smoke_qdrant.py tests/search/test_smoke_qdrant.py
rtk git commit -m "docs(search): add Qdrant recovery runbook"
```

## Acceptance gate

- Hybrid relevance and operational-scale prerequisites are both evidenced.
- Candidate collection exactly reconciles against local IDs and hashes.
- Retrieval quality remains within 0.5 points of the adopted local hybrid baseline.
- TLS, API key, payload indexes, timeouts, and monitoring are configured.
- Snapshot restore is executed, not merely documented.
- Alias cutover and rollback are tested in under five minutes.
- The prior collection is retained for the agreed rollback window.

