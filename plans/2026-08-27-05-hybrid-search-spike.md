# Dense and Hybrid Search Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine with held-out evidence whether multilingual dense retrieval materially improves the validated FTS5 baseline.

**Architecture:** An optional embedding provider produces versioned local float32 vectors. Exact local cosine retrieval isolates model/chunking quality from vector-database operations. A rank-fusion adapter combines dense and lexical rankings with RRF for evaluation.

**Tech Stack:** Python 3.11+, optional sentence-transformers, NumPy, local `.npy` matrix, JSONL metadata, pytest

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Requires completed Plans 02 and 03 with a frozen held-out evaluation set.
- This is an experiment until the adoption gate passes.
- Qdrant is not introduced in this plan.
- Never mix embeddings from different model IDs, revisions, dimensions, or normalization settings.
- Cache identity is `(text_sha256, model_id, model_revision)`.
- Preserve FTS5 as an independent retrieval branch.
- Do not send corpus text to a cloud embedding endpoint by default.

---

### Task 1: Add optional semantic dependencies and ports

**Files:**
- Modify: `pyproject.toml`
- Create: `src/yt_insights/search/semantic.py`
- Test: `tests/search/test_semantic.py`

**Interfaces:**
- Consumes: passage text and model metadata
- Produces: `EmbeddingProvider`, `EmbeddingBatch`, `DenseIndexMetadata`, and `DenseRetriever`

- [ ] **Step 1: Write failing protocol tests**

```python
class FakeEmbeddingProvider:
    model_id = "fake-multilingual"
    model_revision = "test-revision"
    dimension = 3

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray([[1.0, 0.0, 0.0] for _ in texts], dtype=np.float32)


assert isinstance(FakeEmbeddingProvider(), EmbeddingProvider)
```

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_semantic.py -q
```

Expected: import failure for `yt_insights.search.semantic`.

- [ ] **Step 3: Add isolated optional dependencies**

Add:

```toml
semantic = [
  "numpy>=1.26",
  "sentence-transformers>=3.0"
]
```

Core installation and FTS5 tests must continue working without this extra.

- [ ] **Step 4: Implement protocols and metadata validation**

Reject non-float32 vectors, NaN/Inf values, inconsistent dimensions, zero-length model IDs/revisions, and unnormalized vectors when cosine normalization is declared.

- [ ] **Step 5: Run core and semantic tests**

```bash
rtk .venv/bin/pytest tests/search/test_semantic.py -q
rtk .venv/bin/pytest -q
```

Expected: semantic tests pass with NumPy installed; core imports do not import sentence-transformers.

- [ ] **Step 6: Commit the experimental boundary**

```bash
rtk git add pyproject.toml src/yt_insights/search/semantic.py tests/search/test_semantic.py
rtk git commit -m "experiment(search): define semantic retrieval boundary"
```

### Task 2: Implement versioned local embedding cache

**Files:**
- Create: `src/yt_insights/search/embedding_cache.py`
- Test: `tests/search/test_embedding_cache.py`

**Interfaces:**
- Consumes: ordered passage IDs, text hashes, model metadata, normalized vectors
- Produces: `vectors.npy`, `passages.jsonl`, and `metadata.json` under `output/.search/semantic/<cache-key>/`

- [ ] **Step 1: Write failing cache identity tests**

Verify identical text/model/revision is reused, while a changed text hash, model revision, dimension, or normalization invalidates the corresponding cache generation.

- [ ] **Step 2: Write failing atomic-publication tests**

Simulate failure after writing each temporary artifact and assert the previously active cache remains byte-identical and loadable.

- [ ] **Step 3: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_embedding_cache.py -q
```

Expected: import failure for `yt_insights.search.embedding_cache`.

- [ ] **Step 4: Implement deterministic storage**

Store a row-normalized contiguous float32 matrix. JSONL row `n` stores the passage ID and text hash for vector row `n`. Metadata stores model ID, revision, dimension, normalization, passage count, corpus fingerprint, and creation time.

- [ ] **Step 5: Validate before publication**

Reload temporary files, verify shapes/counts/hashes, then atomically replace a small active-generation pointer file. Do not replace three active artifacts independently.

- [ ] **Step 6: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_embedding_cache.py -q
rtk git add src/yt_insights/search/embedding_cache.py tests/search/test_embedding_cache.py
rtk git commit -m "experiment(search): cache embeddings by model revision"
```

### Task 3: Add an explicit local multilingual provider

**Files:**
- Create: `src/yt_insights/search/sentence_transformer_provider.py`
- Test: `tests/search/test_sentence_transformer_provider.py`

**Interfaces:**
- Consumes: model ID `intfloat/multilingual-e5-small`, explicit resolved revision, batch size
- Produces: normalized query and passage embeddings

- [ ] **Step 1: Write tests with a fake sentence-transformer model**

Assert passage input is prefixed with `passage: `, query input with `query: `, batching preserves order, output is float32, and every non-zero row is normalized to unit length.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_sentence_transformer_provider.py -q
```

Expected: provider is absent.

- [ ] **Step 3: Implement lazy optional import**

Import sentence-transformers only inside provider construction. If unavailable, raise an actionable error instructing installation of `yt-insights[semantic]`.

- [ ] **Step 4: Require a resolved revision**

Reject `main`, empty revision, or an unrecorded revision. Persist the resolved immutable revision returned by the model cache. A rerun against a different revision creates a new cache generation.

- [ ] **Step 5: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_sentence_transformer_provider.py -q
rtk git add src/yt_insights/search/sentence_transformer_provider.py tests/search/test_sentence_transformer_provider.py
rtk git commit -m "experiment(search): embed multilingual passages locally"
```

### Task 4: Implement exact dense retrieval and RRF

**Files:**
- Create: `src/yt_insights/search/dense.py`
- Create: `src/yt_insights/search/fusion.py`
- Test: `tests/search/test_dense.py`
- Test: `tests/search/test_fusion.py`

**Interfaces:**
- Consumes: normalized query vector, vector matrix, metadata, and ranked lexical/dense hits
- Produces: exact dense top-k and `reciprocal_rank_fusion(rankings, *, k=60)`

- [ ] **Step 1: Write exact dense-ranking tests**

Use a hand-built normalized matrix and assert NumPy matrix multiplication returns the same order and scores as hand-calculated cosine similarity. Test metadata filters after over-fetching candidates.

- [ ] **Step 2: Write exact RRF tests**

Use the formula:

```python
score(document) = sum(1.0 / (60 + rank) for rank in document_ranks)
```

Assert stable tie-breaking by lexical rank, then dense rank, then passage ID.

- [ ] **Step 3: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_dense.py tests/search/test_fusion.py -q
```

Expected: imports fail because dense and fusion modules are absent.

- [ ] **Step 4: Implement exact dense retrieval**

Memory-map `vectors.npy`, embed the query once, compute the dot product, and select top candidates with `numpy.argpartition` followed by deterministic sorting. Never expose raw cosine scores as comparable to BM25 values.

- [ ] **Step 5: Implement RRF on identities**

Fuse by passage ID, preserve each retriever's original rank and score as diagnostics, then reuse the existing service's video grouping.

- [ ] **Step 6: Run and commit**

```bash
rtk .venv/bin/pytest tests/search/test_dense.py tests/search/test_fusion.py -q
rtk git add src/yt_insights/search/dense.py src/yt_insights/search/fusion.py tests/search/test_dense.py tests/search/test_fusion.py
rtk git commit -m "experiment(search): compare dense and hybrid rankings"
```

### Task 5: Run the controlled three-way experiment

**Files:**
- Create: `scripts/benchmark_hybrid_search.py`
- Test: `tests/search/test_benchmark_hybrid.py`
- Create after execution: `plans/decisions/HYBRID-SEARCH-DECISION.md`

**Interfaces:**
- Consumes: frozen evaluation dataset and the same indexed passages
- Produces: FTS5, dense, and RRF reports plus an explicit adoption decision

- [ ] **Step 1: Write a failing benchmark contract test**

Assert the report records dataset fingerprint, corpus fingerprint, model ID/revision, dimension, chunker version, per-system quality slices, p50/p95 latency, index/build time, cache bytes, and embedding duration.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_benchmark_hybrid.py -q
```

Expected: benchmark script is absent.

- [ ] **Step 3: Implement identical-case evaluation**

Run all three retrievers against one immutable query-case list. Do not tune on the held-out report. Write one JSON result containing every system and slice.

- [ ] **Step 4: Run the experiment**

```bash
rtk .venv/bin/python scripts/benchmark_hybrid_search.py --corpus output --evaluation tests/search/fixtures/evaluation_queries.json --model intfloat/multilingual-e5-small
```

Expected: a report under `output/.search/benchmarks/` and no change to source corpus hashes.

- [ ] **Step 5: Write the decision record**

`HYBRID-SEARCH-DECISION.md` records exact metrics and one outcome:

- `ADOPT`: every hybrid gate in the architecture specification passes;
- `REJECT`: measured improvement is below the gate or exact queries regress too far;
- `UNKNOWN`: evidence is incomplete or the held-out set is invalid.

- [ ] **Step 6: Commit experiment code and decision separately**

```bash
rtk git add scripts/benchmark_hybrid_search.py tests/search/test_benchmark_hybrid.py
rtk git commit -m "experiment(search): benchmark lexical dense and hybrid retrieval"
rtk git add plans/decisions/HYBRID-SEARCH-DECISION.md
rtk git commit -m "docs(search): record hybrid retrieval decision"
```

## Acceptance gate

- Core installation remains functional without semantic extras.
- Every vector cache generation is bound to content, model, revision, and dimension.
- FTS5, dense, and hybrid use the same passages and held-out cases.
- RRF combines ranks, not raw BM25 and cosine scores.
- Adoption occurs only when the specification's measured gates pass.
- Qdrant remains out of scope until both relevance and operational gates pass.

