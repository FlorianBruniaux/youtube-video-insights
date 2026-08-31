# Task 10A E2E Fix Report

## Scope

- Owned change: `tests/research/test_end_to_end.py`.
- Added one filesystem-backed cumulative workflow witness using production `DataPaths`, `rebuild_and_publish_indexes`, and `SQLiteEvidenceReader`.
- Kept production code unchanged and did not assume the pending Task 11 partial-status contract.

## TDD evidence

RED was observed before changing the acquisition fake:

```text
tests/research/test_end_to_end.py::test_complete_cumulative_research_flow_is_durable_and_source_backed
AssertionError: assert tuple(harness.data_paths.root.glob("**/transcripts/*.vtt"))
1 failed
```

The fake now asserts the exact `DataPaths` value supplied by the workflow, writes the acquired VTT under that root, reads the written bytes, and returns their computed SHA-256.

## Real integration witness

The filesystem-backed test proves all of the following in one cumulative scenario:

1. A real initial VTT is indexed into temporary catalogue and search databases.
2. Replacing the live search database during a real SQLite assessment fails retryably and stores no mixed assessment.
3. Discovery returns two candidates and approval is bound to those exact IDs.
4. Acquisition writes one real VTT and returns one `no_transcript` outcome.
5. A forced failure in published-search validation restores the previous catalogue and search database bytes.
6. Retrying reindexing uses the saved outcomes, does not reacquire, performs a real rebuild, and reassesses two sources.
7. The acquired passage and acquisition outcome carry the SHA-256 of the actual acquired VTT.
8. The deterministic dossier manifest hash and dossier hash match their published bytes.
9. Neither `manifest.json` nor `dossier.md` exposes the temporary root, corpus root, or acquired source path.
10. A post-export real rebuild cannot retrieve dossier-only text from the corpus search index.

The path-swap boundary now asserts the error type and filesystem effect, not a fragile substring from an exception message.

## Verification

```text
Focused real witness: 1 passed in 0.17s
E2E file: 10 passed in 0.35s
Full suite: 830 passed, 10 subtests passed in 16.66s
git diff --check: passed
```

Ruff and Mypy are `UNKNOWN` in this worktree because neither module is installed in the shared virtual environment. No dependency installation, network access, global write, or fresh-client canary was performed.

## Limitations

- Provider discovery and acquisition remain deterministic fakes. The databases, reader, refresh, rollback, and export are real local implementations.
- The forced refresh fault patches the existing post-publication validator, matching the lower-level production rollback witness without duplicating SQLite setup.
- Task 11 status and partial-result changes are outside this branch and are not asserted here.
