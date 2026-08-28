# Timestamped Questions and Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate grounded timestamped questions as independent sidecars and preserve verifiable generation provenance without rewriting legacy insights.

**Architecture:** Question generation consumes deterministic transcript passages and an existing `LLMBackend`. Validation is a separate pure step that rejects ungrounded timestamps/evidence before an atomic sidecar write. Search indexes only validated sidecars.

**Tech Stack:** Python 3.11+, dataclasses, hashlib, json, existing backend protocol, pytest, Click

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Requires Plan 01 passage IDs; search itself does not depend on question generation.
- Write questions under `output/<channel>/questions/` as schema-versioned JSON.
- Never mutate existing insight JSON files during this plan.
- Never fabricate model, prompt, transcript hash, or generation time for legacy files.
- Do not cache a truncated, malformed, out-of-range, or ungrounded response.
- A second identical run must perform zero LLM calls unless `--force` is explicit.

---

### Task 1: Define the question sidecar contract

**Files:**
- Create: `src/yt_insights/questions/__init__.py`
- Create: `src/yt_insights/questions/models.py`
- Test: `tests/questions/test_models.py`

**Interfaces:**
- Consumes: passage and generator metadata
- Produces: `QuestionSource`, `GeneratorProvenance`, `TimestampedQuestion`, and `QuestionSidecar`

- [ ] **Step 1: Write failing round-trip tests**

Use this exact shape:

```python
sidecar = QuestionSidecar.from_dict(
    {
        "schema_version": 1,
        "source": {
            "media_id": "nfupYzLjFGc",
            "language": "fr",
            "transcript_sha256": "a" * 64,
        },
        "generator": {
            "backend": "fake",
            "model": "test-model",
            "prompt_id": "questions-v1",
            "prompt_sha256": "b" * 64,
            "generated_at": "2026-08-27T10:00:00Z",
        },
        "questions": [
            {
                "id": "question-1",
                "text": "Comment tester un agent fiable ?",
                "start_seconds": 10.0,
                "end_seconds": 35.0,
                "evidence": "Mesurez les échecs sur les chemins critiques.",
                "source_passage_id": "passage-1",
                "validation": "grounded",
            }
        ],
    }
)
assert sidecar.to_dict()["schema_version"] == 1
```

Test rejection of null timestamps, unknown fields, bad hashes, invalid media IDs, empty evidence, and unsupported schema versions.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_models.py -q
```

Expected: import failure because the questions package does not exist.

- [ ] **Step 3: Implement strict frozen models**

Use explicit `from_dict()` validation rather than permissive `dict.get()` defaults. Question IDs are deterministic hashes of source passage ID, normalized question text, and timestamps.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/questions/test_models.py -q
rtk git add src/yt_insights/questions tests/questions/test_models.py
rtk git commit -m "feat(questions): define grounded sidecar schema"
```

### Task 2: Validate grounding and time boundaries

**Files:**
- Create: `src/yt_insights/questions/validation.py`
- Test: `tests/questions/test_validation.py`

**Interfaces:**
- Consumes: `validate_question(candidate: TimestampedQuestion, passage: Passage) -> TimestampedQuestion`
- Produces: a validated question or `QuestionValidationError`

- [ ] **Step 1: Write failing validation tests**

Cover:

- start before passage start;
- end after passage end;
- end not greater than start;
- unknown passage ID;
- evidence absent from normalized passage text;
- evidence matching after Unicode normalization and whitespace folding;
- a valid grounded question.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_validation.py -q
```

Expected: import failure for `yt_insights.questions.validation`.

- [ ] **Step 3: Implement deterministic validation**

Normalize evidence and passage body with Unicode NFKC, case folding, and collapsed whitespace. Require the normalized evidence to be a literal substring of the normalized passage. Require timestamps inside the passage bounds with a 0.5-second floating-point tolerance.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/questions/test_validation.py -q
rtk git add src/yt_insights/questions/validation.py tests/questions/test_validation.py
rtk git commit -m "feat(questions): validate evidence and timestamps"
```

### Task 3: Generate questions passage by passage

**Files:**
- Create: `src/yt_insights/questions/generator.py`
- Test: `tests/questions/test_generator.py`
- Read: `src/yt_insights/backends/base.py`
- Read: `src/yt_insights/shorts.py`

**Interfaces:**
- Consumes: `generate_questions(document, passages, backend, config, *, prompt_text) -> QuestionSidecar`
- Produces: one to three validated questions per selected passage

- [ ] **Step 1: Write failing backend tests**

Use `FakeBackend` to verify exact `max_tokens` and `timeout`, provenance model/backend capture, and one model call per selected passage. Add cases for valid JSON, fenced JSON, malformed JSON, and `stop_reason == "max_tokens"`.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_generator.py -q
```

Expected: import failure for `yt_insights.questions.generator`.

- [ ] **Step 3: Implement the versioned prompt contract**

The prompt instructs the backend to return only a JSON array with:

```json
[
  {
    "question": "...",
    "start_seconds": 10.0,
    "end_seconds": 35.0,
    "evidence": "exact normalized transcript excerpt"
  }
]
```

Include the passage ID, bounded timestamps, and passage text. Compute `prompt_sha256` from the complete prompt template and expose `prompt_id="questions-v1"`.

- [ ] **Step 4: Implement strict parsing and validation**

Parse the complete response after removing one optional Markdown fence. Do not use a greedy regular expression to extract arbitrary embedded JSON. Reject non-lists, unknown keys, missing fields, and candidates failing `validate_question()`.

- [ ] **Step 5: Enforce the truncation gate**

Raise `QuestionGenerationIncomplete` immediately when `stop_reason == "max_tokens"`. The caller catches this and performs no write.

- [ ] **Step 6: Deduplicate candidates**

Casefold normalized question text and remove exact duplicates. For remaining candidates with Jaccard token similarity above `0.85`, keep the candidate with the longer grounded evidence.

- [ ] **Step 7: Run and commit**

```bash
rtk .venv/bin/pytest tests/questions/test_generator.py -q
rtk git add src/yt_insights/questions/generator.py tests/questions/test_generator.py
rtk git commit -m "feat(questions): generate grounded passage questions"
```

### Task 4: Add atomic cache and idempotency

**Files:**
- Create: `src/yt_insights/questions/store.py`
- Test: `tests/questions/test_store.py`

**Interfaces:**
- Consumes: sidecar path and `QuestionSidecar`
- Produces: `load_valid_sidecar(path, expected_source_hash, expected_prompt_hash)` and `write_sidecar_atomic(path, sidecar)`

- [ ] **Step 1: Write failing cache tests**

Verify cache hits require matching schema, transcript hash, prompt hash, and model. Verify stale, corrupt, and partial sidecars are cache misses. Verify a simulated `os.replace()` failure leaves the previous file byte-identical.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_store.py -q
```

Expected: import failure for `yt_insights.questions.store`.

- [ ] **Step 3: Implement cache validation and atomic writes**

Write UTF-8 JSON with sorted keys and indentation to `<name>.tmp.json`, flush and `os.fsync()` the file, then publish with `os.replace()`. Remove only the exact temporary sibling after a handled failure.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/questions/test_store.py -q
rtk git add src/yt_insights/questions/store.py tests/questions/test_store.py
rtk git commit -m "feat(questions): cache sidecars atomically"
```

### Task 5: Expose a dry-run-first CLI pilot

**Files:**
- Create: `src/yt_insights/cli_questions.py`
- Modify: `src/yt_insights/cli.py`
- Modify: `src/yt_insights/config.py`
- Test: `tests/questions/test_cli_questions.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: VTT manifest, chunker, backend resolver, generator, and store
- Produces: `yt-insights suggest-questions`

- [ ] **Step 1: Write failing CLI tests**

Cover single `--vtt`, channel filter, `--limit 50`, `--dry-run`, cache hit, `--force`, and malformed response. Assert dry-run and cache-hit paths never resolve a backend.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_cli_questions.py -q
```

Expected: command is absent.

- [ ] **Step 3: Add configuration**

Add `questions_dir: Path = Path("output/questions")` and `YT_INSIGHTS_QUESTIONS_DIR` using existing precedence rules.

- [ ] **Step 4: Implement dry-run output**

Report selected videos, passages, estimated calls, total input characters, backend/model if already configured, and output directory. Dry-run performs no backend health request and no file write.

- [ ] **Step 5: Implement bounded execution**

Require either `--vtt`, `--channel`, or an explicit `--all`; default `--limit` is `50`. Use existing effective-concurrency behavior. Close the backend in `finally`.

- [ ] **Step 6: Update documentation and run all tests**

```bash
rtk .venv/bin/pytest tests/questions -q
rtk .venv/bin/pytest -q
rtk git diff --check
```

Expected: all tests pass and documentation describes sidecars, provenance, dry-run, cache invalidation, and pilot limits.

- [ ] **Step 7: Commit the question CLI**

```bash
rtk git add src/yt_insights/cli_questions.py src/yt_insights/cli.py src/yt_insights/config.py tests/questions/test_cli_questions.py README.md
rtk git commit -m "feat(questions): expose grounded generation CLI"
```

### Task 6: Add backward-compatible provenance to future insights

**Files:**
- Modify: `src/yt_insights/analyzer.py`
- Modify: `src/yt_insights/reporter.py`
- Test: `tests/test_analyzer.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes: current five-key insight JSON and optional `_meta`
- Produces: legacy-compatible reads and provenance on newly generated insights

- [ ] **Step 1: Write failing legacy and new-schema tests**

Assert existing five-key fixtures still load unchanged. Assert a newly generated insight contains:

```json
"_meta": {
  "schema_version": 1,
  "backend": "fake",
  "model": "test-model",
  "prompt_id": "insight-v1",
  "prompt_sha256": "...",
  "transcript_sha256": "...",
  "generated_at": "..."
}
```

Assert reporters ignore `_meta` as content but preserve it when round-tripping JSON.

- [ ] **Step 2: Verify focused tests fail**

```bash
rtk .venv/bin/pytest tests/test_analyzer.py tests/test_reporter.py -q
```

Expected: new provenance assertions fail while legacy tests continue to characterize current behavior.

- [ ] **Step 3: Implement optional metadata**

Compute hashes from the complete prompt template and transcript input. Obtain model/backend identifiers from resolved configuration and backend type. Use UTC ISO-8601. Legacy files expose `None` provenance rather than inferred values.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/test_analyzer.py tests/test_reporter.py -q
rtk .venv/bin/pytest -q
rtk git add src/yt_insights/analyzer.py src/yt_insights/reporter.py tests/test_analyzer.py tests/test_reporter.py
rtk git commit -m "feat(insights): record generation provenance"
```

### Task 7: Feed validated questions into search

**Files:**
- Create: `src/yt_insights/questions/search_source.py`
- Modify: `src/yt_insights/search/manifest.py`
- Test: `tests/questions/test_search_source.py`
- Test: `tests/search/test_manifest.py`

**Interfaces:**
- Consumes: `iter_question_passages(root: Path, documents: Mapping[str, DocumentRef]) -> Iterator[Passage]`
- Produces: one `kind="question"` passage per grounded sidecar question

- [ ] **Step 1: Write failing sidecar-to-passage tests**

Assert each validated question maps to the matching media ID/language document, copies question text to `Passage.question`, copies evidence to `Passage.text`, preserves timestamps and source passage ID in deterministic identity input, and emits no transcript mutation.

- [ ] **Step 2: Write failing corrupt-source tests**

Assert an unknown document, transcript-hash mismatch, unsupported schema, or non-grounded validation status blocks manifest publication with a relative-path diagnostic.

- [ ] **Step 3: Verify failure**

```bash
rtk .venv/bin/pytest tests/questions/test_search_source.py tests/search/test_manifest.py -q
```

Expected: question search-source adapter is absent and manifest contains no question passages.

- [ ] **Step 4: Implement strict question passage loading**

Load sidecars through `QuestionSidecar.from_dict()`, verify their transcript hash against the matched manifest VTT, then yield question passages after transcript/insight passages for that document. Never parse sidecars directly inside the SQLite adapter.

- [ ] **Step 5: Run complete verification and commit**

```bash
rtk .venv/bin/pytest tests/questions/test_search_source.py tests/search/test_manifest.py -q
rtk .venv/bin/pytest -q
rtk git add src/yt_insights/questions/search_source.py src/yt_insights/search/manifest.py tests/questions/test_search_source.py tests/search/test_manifest.py
rtk git commit -m "feat(search): index grounded question sidecars"
```

## Acceptance gate

- Every written sidecar passes strict schema validation.
- Every timestamp is within its source passage.
- Every evidence excerpt is found in normalized transcript text.
- Truncated and malformed responses produce no cache write.
- An identical second run performs zero LLM calls.
- A 50-video pilot reaches at least 90% human grounding accuracy and less than 10% near-duplicates.
- Legacy insights remain readable and retain unknown provenance.
- Every validated question is available as a dedicated search passage after rebuild.
- Corrupt or stale question sidecars block publication instead of disappearing silently.
- The complete test suite passes.
