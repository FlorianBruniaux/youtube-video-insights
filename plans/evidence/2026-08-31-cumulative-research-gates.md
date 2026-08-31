# Cumulative research pilot gates, 31 August 2026

`global_activation_ready` is `false`.

| Gate | Status | Evidence boundary |
| --- | --- | --- |
| Relevance pilot | `UNKNOWN` | The new 50-VTT representative index returned no results for the four real pilot queries. No human judgment was invented. |
| Discovery probe | `PASS` | Each exact `ytsearch10:` probe exited 0 with 10 distinct candidate IDs; corpus and both active databases kept identical before/after hashes. |
| Refresh performance | `PASS` | Five isolated full refreshes validated successfully; nearest-rank p95 was 47.122951 seconds, below 60 seconds. |

## Reproducible inputs and commands

Code commit measured: `2430c0fa7002d7827779a11e3117e51f0e975393`.

Corpus fingerprint: `021c20ab0e21ca189d3478dd218fe8732752deb5cb160409f9258314a8380ba6`, computed as SHA-256 over sorted relative VTT paths and each source's SHA-256. It covers 3,332 VTT files. The active catalog hash was `d726f26a478c7891c6c46bf9b7660b5cc14f1e39c6f2ee45d7255b1b2684ee7c`; the active search-index hash was `0ae7b5f6409408994ba3d0d3e2d509e4a8d7607bd0d950895008e5ff7df16e24`, both identical before and after discovery.

```bash
python -m json.tool plans/evidence/2026-08-31-cumulative-research-pilot-queries.json >/dev/null
rg -n 'REPLACE_WITH|TODO|TBD' plans/evidence/2026-08-31-cumulative-research-pilot-queries.json
PYTHONPATH=src /Users/florianbruniaux/Sites/perso/yt-insights/.venv/bin/yt-insights index \
  --corpus-root '<LOCAL_CORPUS_ROOT>' \
  --database /private/tmp/yt-insights-task0.dOEsXX/representative-search-v1.sqlite3 \
  --limit 50 --selection representative
PYTHONPATH=src /Users/florianbruniaux/Sites/perso/yt-insights/.venv/bin/python \
  scripts/prepare_search_relevance_evaluation.py \
  --database /private/tmp/yt-insights-task0.dOEsXX/representative-search-v1.sqlite3 \
  --queries-file plans/evidence/2026-08-31-cumulative-research-pilot-queries.json \
  --output /private/tmp/yt-insights-task0.dOEsXX/pilot-evaluation-packet.json \
  --commit-sha 2430c0fa7002d7827779a11e3117e51f0e975393 --top-k 10
```

The query JSON validated and contains no placeholder marker. Its SHA-256 is `246bc2b6157a886e7f21f87ca23447a1af8de36b98a9057f6d03e1e50e32c2e4`.

The checked-in gate receipt is validated before use. The validator rejects unknown top-level and nested keys, malformed or missing fingerprints, invalid statuses, refresh sample counts other than five, discovery subject counts other than three, and a true global-activation flag unless every external gate passes.

```bash
/Users/florianbruniaux/Sites/perso/yt-insights/.venv/bin/python \
  scripts/validate_cumulative_research_gates.py \
  plans/evidence/2026-08-31-cumulative-research-gates.json
```

Output:

```text
gate evidence valid
```

The representative build selected 50 valid VTTs, generated 3,293 passages, and produced database SHA-256 `9e4d8ff3bc56be56f82416ad0c3c5ca67d72c274f2630a2852952ff4ead8be20` (10,940,416 bytes). The unreviewed packet is at `/private/tmp/yt-insights-task0.dOEsXX/pilot-evaluation-packet.json`, SHA-256 `2cefde38d43742849ce1e371557eff51f2f1ca7f7b199050432517f1fe7aee10`.

The packet has four pilot queries and three subjects, but zero returned rank-1-to-5 results rather than the expected 20. Its observed judgment count and observed relevant count are both zero. Its evaluation status remains `UNKNOWN`; zero null judgments reflects zero result rows, not review completion.

## No-write discovery probes

The three parallel commands were:

```bash
yt-dlp --flat-playlist --skip-download --no-warnings --dump-single-json \
  'ytsearch10:AI workflows in product and engineering teams'
yt-dlp --flat-playlist --skip-download --no-warnings --dump-single-json \
  'ytsearch10:local LLM inference cost MLX Ollama'
yt-dlp --flat-playlist --skip-download --no-warnings --dump-single-json \
  'ytsearch10:AI code quality rules testing code review'
```

All exited 0. They returned 10 distinct candidates each. Flat-playlist metadata did not expose publication dates, recorded as unavailable rather than inferred. The raw JSON, stderr, exit receipts, and candidate titles/channels are retained outside Git in `/private/tmp/yt-insights-task0.dOEsXX/discovery-*.{json,stderr,exit}`.

## Full-refresh measurements

Each run used a fresh temporary database and then `yt-insights index --status`:

```bash
PYTHONPATH=src /Users/florianbruniaux/Sites/perso/yt-insights/.venv/bin/yt-insights index \
  --corpus-root '<LOCAL_CORPUS_ROOT>' \
  --database /private/tmp/yt-insights-task0.dOEsXX/refresh-N/search-v1.sqlite3 --all
PYTHONPATH=src /Users/florianbruniaux/Sites/perso/yt-insights/.venv/bin/yt-insights index \
  --database /private/tmp/yt-insights-task0.dOEsXX/refresh-N/search-v1.sqlite3 --status
```

| Run | Wall seconds | Build / validation exit | Documents / passages | DB bytes | SHA-256 |
| --- | ---: | --- | --- | ---: | --- |
| 1 | 43.312936 | 0 / 0 | 3332 / 184636 | 553832448 | `80b2cba13a1535271bdd3f5a44752ec3e401ec33d0862d83ae9a3be8fdfb2942` |
| 2 | 45.000720 | 0 / 0 | 3332 / 184636 | 553832448 | `62d5a9079006e62db946dc9b351d65de40e82c2791b7ef70ed3fc7fdf215e2e7` |
| 3 | 45.529773 | 0 / 0 | 3332 / 184636 | 553832448 | `761af58f50f15ce002b789042f01bc04f955fd1e51ef73c06f88ff51d8b9b9f8` |
| 4 | 47.122951 | 0 / 0 | 3332 / 184636 | 553832448 | `e00513d75c0bfb047dba33952b88e57d4f82e4a25f64ee6b560f4a369743dd56` |
| 5 | 43.737817 | 0 / 0 | 3332 / 184636 | 553832448 | `04c6e2bc221a548e4b1a95388765a9a30aad0a4e4edfa0013a6abf2b6fe00dba` |

Nearest-rank p95 is 47.122951 seconds. `incremental_refresh_required=false`. No performance warning is triggered, but this does not authorize global activation.

## Concerns

The required 20 unreviewed rank-1-to-5 rows were not generated because the real representative index returned zero rows for every pilot query. Rebuild or revise the representative corpus before requesting human relevance review. Discovery candidate retrieval is not a relevance judgment, and no candidate was treated as approved for acquisition. Temporary raw evidence is intentionally outside Git and will not survive routine temporary-directory cleanup.
