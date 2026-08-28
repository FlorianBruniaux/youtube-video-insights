# Corpus Manifest and Passage Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a deterministic, validated manifest of local VTT and insight sources plus timestamped/search-enrichment passages without modifying the corpus.

**Architecture:** A pure corpus adapter discovers source files and emits immutable domain values. A separate pure chunker converts parsed VTT segments into stable passages, allowing the index and evaluation tooling to consume the same output.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, hashlib, existing VTT parser, pytest

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Branch from the verified search baseline produced by Plan 00.
- Read corpus files only; write tests exclusively under pytest temporary directories.
- Store relative paths, never machine-specific absolute paths.
- Treat `(source_type, media_id, language)` as the document identity.
- Preserve every valid language variant.
- Reject symlinks that resolve outside the configured corpus root.
- Do not add a new runtime dependency.

---

### Task 1: Define immutable search-domain values

**Files:**
- Create: `src/yt_insights/search/__init__.py`
- Create: `src/yt_insights/search/models.py`
- Test: `tests/search/test_models.py`

**Interfaces:**
- Consumes: Python standard-library `date`, `Path`, `Literal`, and `dataclass`
- Produces: `DocumentRef`, `Passage`, `CorpusEntry`, `CorpusManifest`, and `BuildReport`

- [ ] **Step 1: Write failing identity tests**

Add tests proving that two `DocumentRef` objects with the same source, media ID, and language derive the same document ID, while `fr` and `en` derive different IDs:

```python
def test_document_id_preserves_language_variants(tmp_path: Path) -> None:
    fr = make_document_ref(tmp_path, language="fr")
    en = make_document_ref(tmp_path, language="en")

    assert fr.document_id != en.document_id
    assert fr.document_id == make_document_ref(tmp_path, language="fr").document_id
```

- [ ] **Step 2: Run the focused test and verify failure**

Run:

```bash
rtk .venv/bin/pytest tests/search/test_models.py -q
```

Expected: import failure because `yt_insights.search.models` does not exist.

- [ ] **Step 3: Implement the domain values**

Implement frozen dataclasses matching the specification. Use this deterministic helper:

```python
def stable_id(*parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

`DocumentRef.create()` derives `document_id` from source type, media ID, and normalized language. `Passage.create()` derives `text_sha256` from normalized text and derives `passage_id` from document ID, kind, ordinal, timestamps, text hash, subject, tools, and question fields.

- [ ] **Step 4: Export only the stable public values**

`src/yt_insights/search/__init__.py` must export the five domain types and `stable_id`; it must not import SQLite or Click.

- [ ] **Step 5: Run focused tests**

Run:

```bash
rtk .venv/bin/pytest tests/search/test_models.py -q
```

Expected: all model tests pass.

- [ ] **Step 6: Commit the domain contract**

```bash
rtk git add src/yt_insights/search/__init__.py src/yt_insights/search/models.py tests/search/test_models.py
rtk git commit -m "feat(search): define corpus domain models"
```

### Task 2: Parse YouTube source identities safely

**Files:**
- Create: `src/yt_insights/search/corpus.py`
- Test: `tests/search/test_corpus.py`
- Test fixture: `tests/fixtures/search/channel/transcripts/20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt`

**Interfaces:**
- Consumes: a corpus root and VTT paths
- Produces: `parse_youtube_vtt(root: Path, path: Path) -> DocumentRef`

- [ ] **Step 1: Write failing filename tests**

Cover:

```python
@pytest.mark.parametrize(
    ("name", "media_id", "language"),
    [
        ("20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt", "nfupYzLjFGc", "fr"),
        ("Title with [notes] [rAfAnJcuymo].en.vtt", "rAfAnJcuymo", "en"),
    ],
)
def test_parse_youtube_vtt_uses_final_video_token(name, media_id, language, tmp_path):
    path = create_vtt(tmp_path, name)
    document = parse_youtube_vtt(tmp_path, path)
    assert document.media_id == media_id
    assert document.language == language
```

Also test invalid video IDs, missing language suffixes, and a symlink escaping the corpus root.

- [ ] **Step 2: Verify the focused tests fail**

Run:

```bash
rtk .venv/bin/pytest tests/search/test_corpus.py -q
```

Expected: failures because `parse_youtube_vtt` is not implemented.

- [ ] **Step 3: Implement strict identity parsing**

Use an end-anchored expression equivalent to:

```python
YOUTUBE_VTT_RE = re.compile(
    r"^(?P<title>.+) \[(?P<media_id>[A-Za-z0-9_-]{11})\]\.(?P<language>[A-Za-z0-9-]+)\.vtt$"
)
```

Validate `path.resolve().is_relative_to(root.resolve())`. Derive the channel from the first path component below the root rather than from arbitrary title text.

- [ ] **Step 4: Run the filename and boundary tests**

Run:

```bash
rtk .venv/bin/pytest tests/search/test_corpus.py -q
```

Expected: all identity and boundary tests pass.

- [ ] **Step 5: Commit the source adapter**

```bash
rtk git add src/yt_insights/search/corpus.py tests/search/test_corpus.py tests/fixtures/search
rtk git commit -m "feat(search): parse corpus source identities"
```

### Task 3: Build a complete fail-closed manifest

**Files:**
- Modify: `src/yt_insights/search/corpus.py`
- Modify: `src/yt_insights/search/models.py`
- Test: `tests/search/test_corpus.py`

**Interfaces:**
- Consumes: `scan_corpus(root: Path) -> CorpusManifest`
- Produces: classified entries with status `indexable`, `excluded`, or `invalid`; deterministic corpus fingerprint

- [ ] **Step 1: Write failing reconciliation tests**

Create a fixture corpus containing two language variants, their valid insight JSON files, one malformed VTT name, one IFTTD Markdown file, and one unrelated JSON. Assert every recognized source is classified and the two VTT documents form one multilingual group.

```python
assert manifest.discovered == 7
assert manifest.indexable == 4
assert manifest.excluded == 2
assert manifest.invalid == 1
assert manifest.discovered == manifest.indexable + manifest.excluded + manifest.invalid
assert manifest.multilingual_groups == 1
```

Assert a second scan produces the same ordered entries and fingerprint.

- [ ] **Step 2: Run the reconciliation tests and verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_corpus.py -q
```

Expected: failure because `scan_corpus` and manifest counters are absent.

- [ ] **Step 3: Implement deterministic discovery**

Discovery rules:

- recursively sort candidate VTT paths by relative POSIX path;
- discover per-video JSON only below an `insights/` directory and parse the same final media ID/language suffix as its matching VTT;
- classify aggregate/report JSON and unrelated JSON as `excluded` with reason `non_video_json`;
- match valid insights to the exact media ID/language document and classify an unmatched insight as `excluded` with reason `orphan_insight`;
- parse insight fields without rewriting them; malformed optional insight enrichment is reported as invalid/non-blocking while a malformed VTT remains invalid/blocking;
- classify `output/ifttd/ifttd-transcripts/*.md` as `excluded` with reason `untimestamped_ifttd`;
- classify malformed VTT candidates as `invalid` with a relative path and concrete reason;
- compute SHA-256 by streaming 1 MiB blocks;
- compute the corpus fingerprint from ordered relative path, source hash, size, and classification records.

- [ ] **Step 4: Prove a source mutation changes only the fingerprinted entry**

Add a test that changes one fixture VTT, rescans, and verifies exactly one entry hash and the corpus fingerprint change.

- [ ] **Step 5: Run the corpus suite**

```bash
rtk .venv/bin/pytest tests/search/test_corpus.py -q
```

Expected: all corpus tests pass.

- [ ] **Step 6: Commit manifest generation**

```bash
rtk git add src/yt_insights/search/models.py src/yt_insights/search/corpus.py tests/search/test_corpus.py
rtk git commit -m "feat(search): build deterministic corpus manifest"
```

### Task 4: Build deterministic timestamped passages

**Files:**
- Create: `src/yt_insights/search/chunker.py`
- Test: `tests/search/test_chunker.py`
- Read: `src/yt_insights/vtt_parser.py`

**Interfaces:**
- Consumes: `chunk_segments(document: DocumentRef, segments: Sequence[dict]) -> list[Passage]`
- Produces: transcript passages using `TARGET_WORDS=160`, `MAX_WORDS=220`, `MAX_SECONDS=90.0`, and `OVERLAP_SECONDS=12.0`

- [ ] **Step 1: Write failing chunk-boundary tests**

Use synthetic segments at known timestamps and assert:

```python
assert passages[0].start_seconds == 0.0
assert passages[0].end_seconds <= 90.0
assert passages[1].start_seconds < passages[0].end_seconds
assert [p.ordinal for p in passages] == list(range(len(passages)))
```

Also assert no segment text is split, empty input returns an empty list, and identical input yields identical passage IDs.

- [ ] **Step 2: Verify the chunker tests fail**

```bash
rtk .venv/bin/pytest tests/search/test_chunker.py -q
```

Expected: import failure for `yt_insights.search.chunker`.

- [ ] **Step 3: Implement the minimal deterministic chunker**

Accumulate complete segments until either the target word count is met or the maximum duration/word count would be exceeded. Begin the next window from the earliest prior segment within the 12-second overlap. Guarantee forward progress when a single segment exceeds a target.

- [ ] **Step 4: Add a parser integration test**

Parse the existing `sample_fr_vtt` fixture with `parse_vtt_timestamped()`, then chunk it. Assert passage text is non-empty, timestamps are monotonic, and every passage refers to the supplied document ID.

- [ ] **Step 5: Run focused and complete tests**

```bash
rtk .venv/bin/pytest tests/search/test_chunker.py tests/search/test_corpus.py tests/search/test_models.py -q
rtk .venv/bin/pytest -q
```

Expected: focused tests and the complete suite pass.

- [ ] **Step 6: Commit passage chunking**

```bash
rtk git add src/yt_insights/search/chunker.py tests/search/test_chunker.py
rtk git commit -m "feat(search): chunk timestamped transcripts"
```

### Task 5: Add a read-only corpus census command boundary

**Files:**
- Create: `src/yt_insights/search/manifest.py`
- Test: `tests/search/test_manifest.py`

**Interfaces:**
- Consumes: `build_manifest(root: Path) -> CorpusManifest` and `iter_passages(root: Path, manifest: CorpusManifest) -> Iterator[Passage]`
- Produces: a serializable dry-run report and a streaming passage iterator; no Click dependency

- [ ] **Step 1: Write a failing dry-run report test**

Assert `render_manifest_json()` emits sorted relative paths, counters, fingerprint, and explicit exclusion reasons without transcript text or absolute paths.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/search/test_manifest.py -q
```

Expected: import failure for `yt_insights.search.manifest`.

- [ ] **Step 3: Implement orchestration and serialization**

For each indexable VTT, call `parse_vtt_timestamped()` and `chunk_segments()` lazily. After its transcript passages, yield one untimestamped insight passage when a valid matched insight exists: map subject and tool names to dedicated fields and concatenate key points, advice, and quotes into `text`. Yield passages one document at a time so the complete 1 GiB corpus is never retained as Python objects. Convert parser or file errors to manifest-invalid entries; do not silently skip them.

- [ ] **Step 4: Run complete tests**

```bash
rtk .venv/bin/pytest -q
```

Expected: complete suite passes.

- [ ] **Step 5: Commit the manifest use case**

```bash
rtk git add src/yt_insights/search/manifest.py tests/search/test_manifest.py
rtk git commit -m "feat(search): expose corpus manifest use case"
```

## Acceptance gate

- Fixture discovery is deterministic and fully reconciled.
- Invalid indexable sources fail closed with relative-path diagnostics.
- Language variants remain independent documents.
- Passage IDs remain stable across identical builds.
- Full current-corpus dry-run reports 3,270 VTT files and 140 multilingual groups.
- It separately reports 3,219 per-video insight JSON files and 3,083 insight media identities.
- Every valid matched insight produces exactly one `kind="insight"` passage; invalid or orphan insights are visible in diagnostics.
- The dry-run changes no source file hash.
- The complete test suite passes.
