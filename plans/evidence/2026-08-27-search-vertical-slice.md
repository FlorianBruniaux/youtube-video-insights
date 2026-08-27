# Phase 1A — evidence: 50-source local search vertical slice

**Execution date:** 2026-08-28  
**Plan filename retained:** `2026-08-27-search-vertical-slice.md`  
**Worktree / verified HEAD:** `/private/tmp/yt-insights-search-baseline` at `43c04ca` (`codex/search-baseline`); the search implementation baseline remains `b28e9c5`.

## Scope and verdicts

| Gate | Verdict | Observed evidence |
|---|---|---|
| Deterministic technical 50-source slice | PASS | Same selected-source snapshot, BuildReport, SQLite byte size, and five frozen JSON result sets across two builds. |
| Corpus source immutability | PASS | SHA-256 manifest of the exact selected 50 is identical before and after the run. |
| Returned-hit structural integrity | PASS | 492 returned hits were checked: safe existing relative source, source filename video ID equals URL video ID, non-negative timestamp, and canonical URL/timestamp coherence. |
| Fresh full test suite | PASS | Verifier command exited 0 and captured `93 passed` from `python -m pytest -q` with the verified worktree `src` on `PYTHONPATH`. |
| Editorial relevance | UNKNOWN | No three real article subjects or 20-result human review exists. This run does not assess relevance. |
| Phase 1B | BLOCKED | Requires three real subjects plus human validation of at least 16/20 top-five results. |

No full-corpus index, MCP, research pack, embedding, graph, or UI was run or implemented.

## Reproducible inputs and isolation

- Read-only corpus root: `/Users/florianbruniaux/Sites/perso/yt-insights/output`
- Versioned, fail-closed verifier: `scripts/verify_search_slice.py` (commits `2929204`, `43c04ca`); options are required: `--worktree`, `--corpus`, `--artifact-dir`.
- Derived index path (outside `output`): `/private/tmp/yt-insights-search-phase1a-fix1-20260828-run3/search-v1.sqlite3`
- New, never-reused artifact directory: `/private/tmp/yt-insights-search-phase1a-fix1-20260828-run3` (the verifier rejects an existing directory or any artifact path inside the corpus; it never deletes a directory).
- Selection algorithm: exactly `sorted(corpus_root.glob("**/transcripts/*.vtt"), key=relative_posix_path)[:50]`, which is the same rule as `scan_corpus(..., limit=50)`.
- Corpus candidates discovered: `3270`; selected: `50`; invalid selected sources: `0`.

### Exact selected-source SHA-256 snapshot

Canonical snapshot encoding is compact UTF-8 JSON of the ordered `(source, sha256)` rows below. It was recomputed before and after: both digests are `e0cd30b1835cb0a628a2481018eaa97efe54ef4427b753eec28f324271d8f11c`; ordered rows compare equal (`True`), so the difference is zero.

```text
e837dafae7f49291eb12819766979c10dd2e115d2f45e6e6845a25d3eaa1caba  aidevcon/transcripts/20240716 - Unlocking the Full Potential of AI Assistants for Developers with Peter Guagenti from Tabnine [_OHWxWcHJ1o].en.vtt
29e3acbb2d82bf25c94e10e111b366d42b64c50cbdbcc5a7d07aa6c2b600fd50  aidevcon/transcripts/20240718 - Intercom Co-Founder on AI Autonomy, Adapting Dev to LLMs and what AI Native means, with Des Traynor [O13XnOJcDjA].en.vtt
12bc642564098bfbb081f93e11cd118069acd8d607512778a5ce1b4572fa0a11  aidevcon/transcripts/20240723 - Navigating AI for Testing： Insights on Context and Evaluation with Sourcegraph [ExCpKtFgpHI].en.vtt
f94978dd1a4877264986181ce3edd4a3d964a5f5fc3b5265c7cf9a5976e46725  aidevcon/transcripts/20240730 - Automating Development： AI Beyond Coding Assistants with Devin Stein from Dosu [ah6diDQ9wyw].en.vtt
c4202f0653b568583050c6e25b23f1da537e5fb89a9604fbc1a76ba82fbcff26  aidevcon/transcripts/20240730 - Unpacking Dosu： AI's Role in Assisted Development [dLVKum9rZEo].en.vtt
da06fadb321a7c55aec4bcf88d85691c7c69be5318da7a6e79fbea97fbbad22b  aidevcon/transcripts/20240801 - Monthly Roundup： AI Tool Effectiveness, Context, Fin, and AI Autonomy. [P14H8juv_TI].en.vtt
18a6b6070038207d0285abbde892886c3f8fcf4b9f606813cefae9e6dbb50d08  aidevcon/transcripts/20240806 - Transforming Software Testing with AI： A Chat with Itamar Friedman from Codium AI [l9ed6cP8qhs].en.vtt
1606297d66a8c3293725ffbb9e416f77c12f6b5b75ba8b5f64e4b93af4041e36  aidevcon/transcripts/20240813 - Boosting Developer Productivity with Q Developer： Hands on Insights from James Ward [_cpaFCxZPoE].en.vtt
341c4390412f07be47b08f33fbeadc7cc76c690d7db1829bf7d86ebfa0a5fe92  aidevcon/transcripts/20240813 - Rethinking Software Development： James Ward on AI's Role in Software Testing and Coding [81ZYyI2_cWs].en.vtt
2fe40b1fd90beab788adc9b8804c8b08fd8ac47bae3ff4e129b166bf8c84f02f  aidevcon/transcripts/20240820 - From building CoPilot as GitHub CTO to building a code foundation model at Poolside w⧸Jason Warner [-8lOXbv3DHQ].en.vtt
1ebb52a8d41e2953a760f565ba4d89b353a9ce04664eecd0490426dd5c26a403  aidevcon/transcripts/20240827 - TDD and Generative AI - A perfect developer pairing with Bouke Nijhuis [G1pwPKHA4u0].en.vtt
5fa00fe7b9604e894aaa31ca5eaf155795225efd35019e11d5c7955dd69b52f0  aidevcon/transcripts/20240827 - TDD and Generative AI in Action! [1LX0_GWVmdo].en.vtt
dd480772fcb5ab13bf6913f306b3fead334d4a58a836206fcf889fed8a7ac349  aidevcon/transcripts/20240905 - Monthly Roundup： Gen AI and TDD, Understanding vs Generating Code, Speciality vs General models... [qSKl8DzF7SI].en.vtt
9500b0aaed5cdc89c493a4877337882c9f552c3a392b041e731f23f272535c60  aidevcon/transcripts/20240910 - Enterprise AI Solutions Need to be Different - Glean CPO on RAG, Changing Behavior and BYO-Model. [OPfUMXz34cU].en.vtt
1b0db4b68257d7543d288e2c3abb040da49bb6ca87f64fa9f4821a23e6b0cae5  aidevcon/transcripts/20240917 - AI-Powered Documentation： Insights with Omer Rosenbaum, CTO of Swim [C9NYb9b_pYs].en.vtt
529fb1475da5c9e964e253f94f4bfe6b7ef3abfd085bd0c0b0928e036ec5a19c  aidevcon/transcripts/20240917 - Deep Dive on AI Documentation： Live Demo with Omer Rosenbaum [p-1A1abSR9c].en.vtt
fd0be8d86ee8ef21a3ebc035622fa741fba462894476775941e1598cbf82cdb1  aidevcon/transcripts/20240924 - Does AI Generate Secure Code？ Tackling AppSec in the Face of AI Dev Acceleration & Prompt Injection. [vg-svm2mT7w].en.vtt
d8dfafcfbc48ef99e3ce51aaefe981984e6887f3ec36f311c606f6e223c45c31  aidevcon/transcripts/20241001 - Can Claude 3.5 build production-quality apps without us having to write code？ [yikF4AGen8I].en.vtt
722bb827abdd43c3e07bd0608cdfc4e96ba78146e52d1d6729e5fa87b55cbef8  aidevcon/transcripts/20241001 - Live demo： Front end app creation with Claude 3.5 and Cursor [HvQ4KiF916s].en.vtt
1c93f27d31df51453248beff4ed5df91ac86b75d1e453cb71c18dbf311202ab8  aidevcon/transcripts/20241003 - Monthly Roundup： AI Security, AI Documentation, Enterprise AI Strategies, with Ben Galbraith [ddISeYuDGC8].en.vtt
b7423b2bdfe892e0d59eba036b83b2b51cbba3b7e44cfc955b44aa47e8904e8a  aidevcon/transcripts/20241009 - Armon Dadgar, Hashicorp co-founder, on AI Native DevOps： Can AI shape Autonomous DevOps？ [jhKNccjrseI].en.vtt
8653bb74260e7699cffa1c373e9a82afa8d780e60caed26835e678c1558ab7c9  aidevcon/transcripts/20241014 - In an enterprise having a non-deterministic LLM giving you different answers is far less acceptable. [vR7yiTx__Mk].en.vtt
a44f768822c4a1719bbc3b79f44711234050a44dcf4930df7df0d252fbeead82  aidevcon/transcripts/20241014 - Is AI generated code secure？ [d9iiJlO6OPg].en.vtt
4b999516497d433830e29b95a73fe1b0b103f55c240c927b0b857950ee375304  aidevcon/transcripts/20241015 - AI Security Vulnerability Live Hacking with Liran Tal, from Snyk [7Y67F1LM_gQ].en.vtt
9853dee2c7586db0db0aacc5cd03fc4afe475f7e3fa7408802faced2d5ebbac0  aidevcon/transcripts/20241015 - Can AI Tools Be Trusted with Security-Critical Code？ Real World AI Security Risks with Liran Tal [fWjXc32o9zE].en.vtt
afc9edcd48f4429e5c7980872a53d5cd31e2e5972c971193228485dfbfb09673  aidevcon/transcripts/20241022 - From DevOps to AI： Patrick Debois Shares Strategies for Successful AI Integration and Culture Change [U6zY644PU_4].en.vtt
d1adc2f18e466afd587e78b68162b0799792bce2481fb58586bd84c0015e6ef7  aidevcon/transcripts/20241029 - AI-Powered Documentation Experience with Amara Graham： Kapa below the surface [3LUG9EfH-c0].en.vtt
02ee16ac2d32242a3e21914a8e49234c05edeb23f271f0ada84ae701f336a457  aidevcon/transcripts/20241029 - Changing the Developer Documentation UX Workflow using AI with Amara Graham [Ucz9OgnQvVw].en.vtt
8ad3c37b1edfa460242e19a47625f3c930daf6f0a37a7c2fcb59234a43e0ef35  aidevcon/transcripts/20241107 - Does AI threaten the open web？ Discussion with Netlify's CEO & Co-Founder, Matt Biillmann [WhhGMZo-HLg].en.vtt
70f5c0dbef4c28f379641e7f44a13812b54bfa1890aecc97cb1985b5c77eb932  aidevcon/transcripts/20241112 - Beyond Coding assistants： Cursor as an API, Coding with gestures and more with Patrick Debois [RmvX9Oshfp4].en.vtt
06672a22f0e3c3094928ae6c30f547c9c03f80d327cc6051c4bfae713b673cf9  aidevcon/transcripts/20241120 - Tessl Raises $125M to Build AI Native Development [Y3k6HzkFxVo].en.vtt
73e0e2b463eebd82d6d929e25de8c2c9af50e3fb5f80a59370492f5d5ba35383  aidevcon/transcripts/20241122 - AI Product engineering - how AI is changing our products with Patrick Debois [D_iNSIa3FF4].en.vtt
a506975fd234470fd5e0a846946cfa4de36c2579cdcc8673a311fdcf1e6749fb  aidevcon/transcripts/20241122 - AI Security Risks： The Impact of Generative AI in Developer Workflows with Liran Tal & Ashish Rajan [zm7WDxH7LLw].en.vtt
f80f40cfef36610786ac05f4bc5a227e11c5d114a1c2a2c5ff6ce385ab775811  aidevcon/transcripts/20241122 - AI-Powered Development： Hands-On Techniques for Immediate Impact with Lize Raes [kj5M3mkLpY0].en.vtt
dcacc602172059b21a1465364dfd2cc32c1caf7b7126c83652d41dffd2c09be9  aidevcon/transcripts/20241122 - Agents Observability with OpenLLMetry with Nir Gazit [Ilyhddkh3AI].en.vtt
8a4f73e4e2b16644ebded05c556bc9d74d3f4f4689da507cbbe88d737f633a26  aidevcon/transcripts/20241122 - Automating Code Performance Optimization with AI with Saurabh Misra [Y1Q_TmS0yV4].en.vtt
479471f0cb23cca0625ea8ee8dd5487bba02931dc1e683151ce01fc4bcd84d1d  aidevcon/transcripts/20241122 - Bolt.new shoots us into the future of prompt to deployed app with Eric Simons [YKHdD4AKZ-c].en.vtt
e4464bf89f061b42a535f1d68eec5ccd5e12556050053da35a225600cb53cb52  aidevcon/transcripts/20241122 - Fireside Chat with Alex Komoroske on the AI Dev Ecosystem [M5e-FwST9W4].en.vtt
3022d58058f11067e3ef916ef274e9d485f3e92a683f2fc4e219c51d689a08a1  aidevcon/transcripts/20241122 - From Prompt Engineering to Flow Engineering： Moving Closer to System 2 Thinking with Itamar Friedman [23v9GBJvcrc].en.vtt
1ef1955d8e5ae170801fb33d90798cb45c335cf03d17168a7f96c8e21a57438a  aidevcon/transcripts/20241122 - Going from v0 to vInfinity with AI and Malte Ubl of Vercel [pPY_bwuS9mo].en.vtt
bd54314ff1f8a90e6a7db9e3c056fcc07c3f027e9824d87f12703caf141d105d  aidevcon/transcripts/20241122 - Modernizing Systems Observability with AI and LLMs with Jason Hand [LcsGFQr7vqU].en.vtt
e0f3a760b4a9d33ba40691449080a62c68cdc0552f8357be377581649580bac5  aidevcon/transcripts/20241122 - Ordering Files for AI-Assisted Development with Guy Eisenkot [AgZI_hN_rDo].en.vtt
69829e6f01019ba26941aa68be7cfbb1c855e3d0f7941d0a0b7a717b9104981a  aidevcon/transcripts/20241122 - Using Artificial Intelligence, Safely with Tanya Janca [FRWpoRa9JGE].en.vtt
40c0eb986123b8b347887bcc7b6a9a79b95a6190b2763abe729dc21d9f57aebb  aidevcon/transcripts/20241122 - What is AI Native Development - and how can we prepare for the journey with Guy Podjarny [e1a3WuxTY-k].en.vtt
9821cdb9758fda309d3f4c5dd41f38a854fb53e91426e05eab5011dcddaa9798  aidevcon/transcripts/20241122 - Wiring up your AI with Breadboard by Dimitri Glazkov [bdfOJJwnUcc].en.vtt
b84f77784a5912f4beede1f9ee9d821c9864fac8ac5a17477bce9b22129267b7  aidevcon/transcripts/20241126 - Building Notion AI： Lessons Learned and Myths Busted with Simon Last, Notion Co-Founder and CTO [hmPIA1cv3Dg].en.vtt
2b1e7a773cd1d4b88fca435eef803320d8a80f11c079929a3c9cbbf6761ac4e0  aidevcon/transcripts/20241205 - DevOps with AI： Identifying the impact zone, with Roxane Fischer [eOTSG8UXGaw].en.vtt
11bc3b286d6163067c42026a285e507033790f698d47b32da096978ccf76d03a  aidevcon/transcripts/20241210 - AI Evaluations and Testing： How to Know When Your Product Works (or Doesn’t) [gZ4sGROvOdQ].en.vtt
cd884a9e075df9197021ad1cb31bf82e748716baf6078bfad36ce3241d7fcb74  aidevcon/transcripts/20241216 - The Evolution of v0 and Vercel's AI SDK with Malte Ubl [wrRsHlhpubY].en.vtt
f900094a13a2a0199ecd4ee31a282e670d66d4d804bf53e0322dc3d99df0eae3  aidevcon/transcripts/20241231 - Crossover episode with The Infra Pod - AI Native Development with Guy Podjarny [SsOaOVPwW5I].en.vtt
```

## Commands, exit codes, and fail-closed gate

| Command | Exit | Result |
|---|---:|---|
| `python scripts/verify_search_slice.py --worktree /private/tmp/yt-insights-search-baseline --corpus /Users/florianbruniaux/Sites/perso/yt-insights/output --artifact-dir /private/tmp/yt-insights-search-phase1a-fix1-20260828-run3` | 0 | Real run from clean verified HEAD `43c04ca`; writes `results.json` before returning. |
| `PYTHONPATH=src rtk pytest -q` | 0 | Pre-run fresh worktree suite: `93 passed`. |
| `rtk git diff --check` | 0 | Pre-run clean worktree check; the verifier also captures a final clean worktree diff check. |
| `git status --porcelain=v1` (primary, before and after) | 0 / 0 | Full stdout/stderr/exit objects are serialized in the artefact and compared byte-for-byte. |
| `git status --porcelain=v1` (verified worktree, final) | 0 | Serialized in the artifact; both stdout and stderr are empty. |
| `git diff --check` (worktree and primary) | 0 / 0 | Both result objects are serialized by the verifier. |

The verifier loads the actual `yt_insights.cli:cli` group from the explicit worktree `src/` path and invokes it through Click's in-process runner (no RTK assumption in Python). It records the command vector, stdout SHA-256, stderr, exit code, duration, and hit count for each index/search operation. Its compact evidence is `/private/tmp/yt-insights-search-phase1a-fix1-20260828-run3/results.json`; the source list and all frozen/hostile witnesses are retained below. Every critical predicate is computed, persisted in `gates`, and makes the process exit non-zero if false.

## Two-build comparison

| Metric | Build 1 | Build 2 | Verdict |
|---|---:|---:|---|
| CLI exit | 0 | 0 | PASS |
| Build duration (ms) | 8251.079 | 8860.479 | Informational |
| Sources discovered | 3270 | 3270 | PASS |
| Sources selected | 50 | 50 | PASS |
| Invalid sources | 0 | 0 | PASS |
| Documents indexed | 50 | 50 | PASS |
| Passages indexed | 2835 | 2835 | PASS |
| SQLite DB bytes | 9154560 | 9154560 | PASS: identical in this observed run |

SQLite byte identity is observed here, not promised as a cross-platform invariant: SQLite version, page-size defaults, or implementation changes could make equivalent rebuilds differ in bytes. The required semantic comparisons below are the stable gate.

### Frozen JSON searches

Each row compares parsed JSON hit arrays exactly. The SHA-256 is over the deterministic CLI stdout JSON and is supplied as a replay witness.

| Query | B1 exit/hits | B2 exit/hits | JSON SHA-256 (both) | Semantic comparison |
|---|---|---|---|---|
| `artificial intelligence` | 0 / 20 | 0 / 20 | `c0456f8ba9744c2e123eefccd6f2dc6fc05b1efc579b7c427d31e8f4d44cfc1a` | PASS |
| `developer productivity` | 0 / 20 | 0 / 20 | `20b2654eda5c7ac8a998f023070568e2cc8fec661ddd9caa3678a1eaf8289277` | PASS |
| `software development` | 0 / 20 | 0 / 20 | `39c5a075efc831ea8ed86cca3f822cd7bde757dd01e9aaf88fdbe07aadacac59` | PASS |
| `security` | 0 / 20 | 0 / 20 | `cb8ec72e16810dcb0d1bbec8f6ae90e0548d0d28081ceb4cd5596fd125f8f834` | PASS |
| `agents` | 0 / 20 | 0 / 20 | `2f24466ad92e9bc252a11b1cb030bd924c9addbaa09ef839aaf9ad3717f298cb` | PASS |

Query latency sample (B2, Click invocation plus index search): `artificial intelligence` 5298.897 ms; `developer productivity` 5541.064 ms; `software development` 4188.092 ms; `security` 1059.539 ms; `agents` 1226.572 ms.

## Hostile-query results

All queries contain at least one lexical token; special syntax is treated as input text, not trusted FTS grammar. `error` is null for every row. Latency measures the verifier's Click invocation and search.

| # | Query | Exit | Hits | Latency (ms) | Error |
|---:|---|---:|---:|---:|---|
| 1 | `"developer"` | 0 | 20 | 1401.682 | null |
| 2 | `ai-driven` | 0 | 20 | 1748.6 | null |
| 3 | `security:` | 0 | 20 | 1729.59 | null |
| 4 | `NEAR developer` | 0 | 1 | 1683.445 | null |
| 5 | `AI OR developer` | 0 | 20 | 1351.334 | null |
| 6 | `agent*` | 0 | 20 | 1348.594 | null |
| 7 | `(developer)` | 0 | 20 | 1481.685 | null |
| 8 | `don't` | 0 | 20 | 1754.39 | null |
| 9 | `design/engineering` | 0 | 12 | 1547.343 | null |
| 10 | `back\end` | 0 | 20 | 1409.391 | null |
| 11 | `évaluation` | 0 | 20 | 1514.706 | null |
| 12 | `"machine learning"` | 0 | 20 | 1525.513 | null |
| 13 | `long-term` | 0 | 15 | 1596.527 | null |
| 14 | `title:developer` | 0 | 3 | 1572.988 | null |
| 15 | `NEAR/5 agent` | 0 | 0 | 1542.963 | null |
| 16 | `OR security` | 0 | 20 | 1393.33 | null |
| 17 | `deploy*` | 0 | 20 | 1305.456 | null |
| 18 | `(AI OR security)` | 0 | 20 | 1222.814 | null |
| 19 | `l'IA` | 0 | 0 | 1284.604 | null |
| 20 | `résumé` | 0 | 1 | 1372.702 | null |

Coverage includes quotes, hyphens, colons, `NEAR`, `OR`, `*`, parentheses, apostrophes, slash, backslash, and Unicode. Result: 20/20 exit 0, 20/20 error null.

## Every returned-hit validation

The fail-closed verifier checked every hit returned by the two frozen rounds and all hostile searches (`492` hits total); `hit_validation_error_count` is `0`. For each payload it verified:

- `source` is a POSIX relative path with no backslash, absolute component, or `..`, remains under the corpus root, and names an existing file.
- The source filename matches the canonical VTT rule and its extracted `[<video_id>]` exactly equals the `<id>` in `url`.
- `url` is canonical `https://youtube.com/watch?v=<id>&t=<seconds>s`; `<id>` matches `[A-Za-z0-9_-]{11}`.
- `timestamp` is canonical `HH:MM:SS`, converts to a non-negative second count, and exactly agrees with the URL `t` parameter.

Validation failures recorded: `0`. This is structural correctness only; it says nothing about editorial relevance.

## Checkout protection

The primary checkout was discovered with `git worktree list --porcelain`. The verifier serializes full command objects (command, cwd, exit, stdout, stderr, duration) for the primary status before/after and requires exact equality of stdout and stderr. Both exits are `0`; the exact protected-change stdout is:

```text
 M .claude/skills/yt-add-channel.md
 M CLAUDE.md
 M runbook/run-channel.sh
?? ROADMAP.md
?? batches/
?? plans/
?? scripts/build_speakers.py
```

The verified run began at committed clean HEAD `43c04ca`: scripts/tests were committed first specifically so `worktree_status_final` could legitimately require empty stdout/stderr. It wrote no corpus source and no primary-checkout production/test/corpus file. All eleven persisted gates are `true`: build commands, build reports, DB bytes, frozen results, hostile queries, snapshot, hit validation, primary status, final worktree status, tests, and diff checks.

## Limit

**UNKNOWN remains UNKNOWN:** no relevance PASS is claimed. Phase 1B remains blocked pending exactly the human inputs stated above. Stop boundary respected.
