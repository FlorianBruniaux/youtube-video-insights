# Search Evaluation and Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make search quality, performance, coverage, and failures measurable before semantic retrieval or UI work is approved.

**Architecture:** A deterministic evaluator consumes reviewed query judgments and normal search results. A benchmark runner measures build/query resources locally and emits a versioned report without recording raw user activity.

**Tech Stack:** Python 3.11+, json, statistics, resource, time/perf_counter, pytest

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Ground truth is reviewed by a human; LLM output is never accepted as truth.
- Keep evaluation queries and expected media IDs version-controlled.
- Keep transcript bodies and generated production indexes out of Git.
- Report metrics by query category and language, not only as one aggregate.
- Benchmark the complete VTT corpus, not only insight summaries.
- Do not add a metrics server or telemetry dependency.

---

### Task 1: Define the evaluation dataset contract

**Files:**
- Create: `tests/search/fixtures/evaluation_queries.schema.json`
- Create: `tests/search/fixtures/evaluation_queries.json`
- Create: `plans/EVALUATION-GUIDE.md`
- Test: `tests/search/test_evaluation_dataset.py`

**Interfaces:**
- Consumes: reviewed query cases
- Produces: a validated dataset with stable case IDs and graded relevance judgments

- [ ] **Step 1: Add a strict JSON Schema**

Each case uses this contract:

```json
{
  "id": "exact-001",
  "query": "claude code hooks",
  "category": "exact",
  "query_language": "en",
  "filters": {"channel": null, "language": null, "after": null, "before": null},
  "judgments": [
    {
      "media_id": "nfupYzLjFGc",
      "language": "fr",
      "relevance": 2,
      "start_min": 0,
      "start_max": 120
    }
  ],
  "review": {"reviewer": "human", "status": "reviewed"}
}
```

Allowed categories are `exact`, `natural_question`, `paraphrase`, `bilingual`, `filter`, `hostile`, and `no_answer`. Relevance is `0`, `1`, or `2`.

- [ ] **Step 2: Add seed cases covering every category**

Add at least two reviewed seed cases per category using real corpus media IDs. The guide explains how to play the expected video, verify the passage window, and set relevance. No case may use `review.status=generated`.

- [ ] **Step 3: Write validation tests**

Validate unique IDs, allowed enums, bounded timestamps, eleven-character YouTube IDs, at least one case per category, and absence of duplicate judgments for the same `(media_id, language)`.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_evaluation_dataset.py -q
rtk git add tests/search/fixtures/evaluation_queries.schema.json tests/search/fixtures/evaluation_queries.json tests/search/test_evaluation_dataset.py plans/EVALUATION-GUIDE.md
rtk git commit -m "test(search): define reviewed relevance dataset"
```

### Task 2: Implement ranking metrics

**Files:**
- Create: `src/yt_insights/search/evaluation.py`
- Test: `tests/search/test_evaluation.py`

**Interfaces:**
- Consumes: `evaluate_cases(cases: Sequence[EvaluationCase], search: Callable[[SearchQuery], list[SearchHit]])`
- Produces: `EvaluationReport` with Recall@5, MRR@10, nDCG@10, zero-result rate, and per-slice metrics

- [ ] **Step 1: Write exact metric tests**

Use tiny rankings with hand-calculated results:

```python
def test_recall_at_five_counts_retrieved_relevant_documents() -> None:
    assert recall_at_k(["a", "x", "b"], {"a", "b"}, 5) == 1.0


def test_mrr_uses_first_relevant_rank() -> None:
    assert reciprocal_rank(["x", "a"], {"a"}, 10) == 0.5
```

Add nDCG tests for perfect, reversed, empty, and no-answer rankings.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_evaluation.py -q
```

Expected: import failure for `yt_insights.search.evaluation`.

- [ ] **Step 3: Implement pure metric functions**

Functions accept document identity tuples rather than SQLite rows. Round only during rendering, never during aggregation.

- [ ] **Step 4: Implement per-category aggregation**

The report contains overall metrics plus exact keys for all seven categories and all present query languages. Missing slices are validation errors rather than silently omitted output.

- [ ] **Step 5: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_evaluation.py -q
rtk git add src/yt_insights/search/evaluation.py tests/search/test_evaluation.py
rtk git commit -m "feat(search): calculate retrieval quality metrics"
```

### Task 3: Build the reproducible benchmark runner

**Files:**
- Create: `scripts/benchmark_search.py`
- Test: `tests/search/test_benchmark_search.py`

**Interfaces:**
- Consumes: corpus root, index path, evaluation dataset, warm-up count, repetitions
- Produces: `output/.search/benchmarks/<timestamp>-fts5-v1.json`

- [ ] **Step 1: Write a failing argument and output test**

Invoke the script against a fixture corpus and assert output contains:

```text
git_commit
python_version
sqlite_version
fts5_enabled
corpus_fingerprint
document_count
passage_count
database_bytes
build_seconds
peak_rss_bytes
query_p50_ms
query_p95_ms
query_p99_ms
quality
```

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_benchmark_search.py -q
```

Expected: failure because the runner does not exist.

- [ ] **Step 3: Implement isolated build measurement**

Use `time.perf_counter()` around the complete manifest plus index build. Use `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` with platform-aware byte conversion. Capture SQLite compile options and fail if `ENABLE_FTS5` is absent.

- [ ] **Step 4: Implement warm query measurement**

Run every case once as warm-up, then run each case five times. Store distributions and aggregate percentiles. Do not persist query strings in the benchmark result; persist case IDs and category metrics.

- [ ] **Step 5: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_benchmark_search.py -q
rtk git add scripts/benchmark_search.py tests/search/test_benchmark_search.py
rtk git commit -m "perf(search): add reproducible corpus benchmark"
```

### Task 4: Expand the reviewed oracle to release size

**Files:**
- Modify: `tests/search/fixtures/evaluation_queries.json`
- Modify: `plans/EVALUATION-GUIDE.md`

**Interfaces:**
- Consumes: real search results and manual video review
- Produces: 60–100 reviewed cases with balanced slices

- [ ] **Step 1: Add the exact-query slice**

Create at least 12 reviewed cases covering tool names, people, acronyms, technical phrases, hyphenated terms, and quoted phrases.

- [ ] **Step 2: Add natural-language and paraphrase slices**

Create at least 20 reviewed cases where query words do not always appear verbatim in the relevant title. Confirm each judgment by inspecting the VTT passage and video timestamp.

- [ ] **Step 3: Add bilingual and filter slices**

Create at least 16 reviewed cases covering FR query to EN transcript, EN query to FR transcript, language variants, channel filters, and inclusive date boundaries.

- [ ] **Step 4: Add hostile and no-answer slices**

Create at least 12 reviewed cases containing FTS punctuation/operators and plausible queries with no relevant result.

- [ ] **Step 5: Run dataset validation**

```bash
rtk .venv/bin/pytest tests/search/test_evaluation_dataset.py -q
```

Expected: 60–100 unique, reviewed, schema-valid cases and every category represented.

- [ ] **Step 6: Commit the reviewed oracle**

```bash
rtk git add tests/search/fixtures/evaluation_queries.json plans/EVALUATION-GUIDE.md
rtk git commit -m "test(search): add reviewed retrieval oracle"
```

### Task 5: Run the lexical release gate

**Files:**
- Create locally, ignored: `output/.search/benchmarks/<timestamp>-fts5-v1.json`
- Modify after review: `plans/README.md`

**Interfaces:**
- Consumes: complete corpus, FTS5 search implementation, reviewed oracle
- Produces: an evidence-backed `PASS`, `FAIL`, or `UNKNOWN` for every release gate

- [ ] **Step 1: Run a clean full-corpus benchmark**

```bash
rtk .venv/bin/python scripts/benchmark_search.py --corpus output --evaluation tests/search/fixtures/evaluation_queries.json --rebuild
```

Expected: a benchmark JSON is written under the ignored output directory.

- [ ] **Step 2: Compare against release gates**

Record exact results for build time under five minutes, RSS under 1 GiB, warm p95 under 100 ms, Recall@5 at least 0.80, and zero hostile-query failures. Do not convert an unmeasured value into a pass.

- [ ] **Step 3: Investigate failed slices before tuning weights**

For each failed category, classify the cause as source coverage, identity parsing, chunking, query normalization, ranking, or ground-truth defect. Change one variable per benchmark run and preserve both reports.

- [ ] **Step 4: Update the implementation tracker**

Mark Plan 03 complete only if every required gate is `PASS`. Mark unmeasured or inaccessible evidence as `UNKNOWN`.

## Acceptance gate

- Dataset contains 60–100 human-reviewed cases.
- Metrics have exact unit tests.
- Benchmark reports runtime and environment versions.
- Query text and transcripts are absent from benchmark logs.
- Results are sliced by category and language.
- Every P0 release gate is recorded as `PASS`, `FAIL`, or `UNKNOWN` with evidence.

