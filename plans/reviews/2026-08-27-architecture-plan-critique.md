# Architecture and Plan Critique Report

**System/Component:** yt-insights local editorial knowledge base

**Date:** 2026-08-27

**Reviewers:** critique-arch and critique-plan skills

**Reviewed artifacts:**

- `plans/specs/SEARCH-ARCHITECTURE.md`
- `plans/README.md`
- `plans/PARALLEL-SESSIONS.md`
- eight implementation plans dated 2026-08-27

## Evidence snapshot

| Measure | Verified value | Consequence |
|---|---:|---|
| Planning volume | 3,067 lines | The plan itself has become a product to maintain |
| Planned tasks | 41 | Critical path is hidden by task volume |
| Planned new files | 42 | Excessive before validating one editorial workflow |
| VTT files | 3,270 | Full-corpus behavior matters |
| Parsed VTT segments | 3,068,045 | Chunking quality is the first retrieval risk |
| Full VTT parse | 34.216 s, zero errors | Parsing is feasible; relevance remains unproven |
| Existing baseline | 14 tests previously verified | Good foundation, not yet integrated into main |
| MCP implementation | None | Primary LLM access path is missing |
| Editorial pack implementation | None | Search results cannot become a persistent article dossier |

---

## Architecture Critique Report

### Architecture summary

The v1 design turns immutable VTT/JSON files into an atomic SQLite FTS5 index, then adds optional question generation, dense retrieval, a local web server, and a conditional Qdrant migration. It optimizes retrieval infrastructure but does not model the user's actual endpoint: a source-backed dossier used by an LLM to prepare an article.

### What's good

- Source files remain authoritative; indexes are derived and rebuildable.
- Mandatory-source failures, safe FTS grammar, atomic index publication, provenance, and dirty-worktree isolation are treated explicitly.

### Blockers

| Issue | Risk | Challenge |
|---|---|---|
| No end-to-end editorial workflow | The system can return hits but cannot persist selection, angle, notes, or an exportable article dossier | What user outcome proves the search engine has value? |
| No MCP despite LLM-first use | The main consumer must shell out manually or wait for a web UI | Why build a custom HTTP UI before exposing the stable search service to the LLM already used for writing? |
| State boundaries stop at search | Derived index data and authored user data have no separate ownership model | What survives an index deletion: searches, selected passages, notes, and dossiers? |

### Major concerns

| Issue | Risk | Simpler alternative |
|---|---|---|
| Retrieval design frozen before a passage spike | Three million parsed segments may produce poor or excessive chunks despite acceptable build time | Validate one deterministic chunker on 50 representative videos and 20 real queries first |
| Arbitrary chunk and BM25 settings | 45–90 seconds, 100–220 words, overlap, and weights look precise without relevance evidence | Treat them as experiment parameters and freeze only the first passing configuration |
| One corrupt optional question sidecar blocks all indexing | Optional enrichment can make mandatory transcript search unavailable | Quarantine invalid optional enrichment, report it, keep valid transcript passages searchable |
| Custom web server is planned before proven human UI need | Maintains HTTP lifecycle, packaging, CSP, browser behavior, and API errors for a single local user | Ship MCP plus static HTML dossier export first |
| Dense/Qdrant plans exist before lexical failure | Dependencies, vector caches, migrations, snapshots, TLS, and recovery add cost with no measured benefit | Keep one decision gate paragraph, not two active implementation plans |
| Dedicated graph direction is undefined | Entity aliases, extraction errors, stale edges, and provenance can create a persuasive but false graph | Add derived SQLite entity tables only after three real graph questions are documented |
| Full rebuild assumes sufficient disk | Atomic replacement needs space for active and temporary databases | Preflight free disk against measured candidate size and keep the previous generation until smoke checks pass |
| MCP egress is not modeled | A local server can still send excerpts to a remote LLM through the host | Bound result size, document egress, and make the MVP read-only over stdio |

### Failure modes not addressed sufficiently

| Scenario | Current consequence | V2 behavior |
|---|---|---|
| yt-dlp or acquisition fails mid-channel | Search plans start after files exist; partial acquisition state is unclear | Preserve prior files, record per-video status, resume only failed/missing videos |
| One VTT is malformed | Whole index publication fails | Block publication and report exact mandatory source path |
| Optional insight/question is malformed | Question plan can block the entire index | Quarantine optional enrichment and expose warning counters |
| Index is corrupt or missing | CLI returns an error | Return unavailable with exact rebuild command; source files and dossiers remain usable |
| Search runs during rebuild | File replacement semantics are assumed | Query active generation read-only; publish by atomic pointer/file swap and test concurrency on macOS |
| MCP process crashes | LLM loses retrieval | CLI remains available; restart MCP without data recovery |
| Remote LLM receives sensitive excerpts | Local-only assumption is misleading | Default bounded excerpts, explicit documentation, no query/result logs |
| Source is renamed or deleted | Stale hits are possible without complete reconciliation | Fingerprint deletion/rename, rebuild, assert zero ghost documents |
| Entity extractor merges two people/tools | Graph returns false relations | Every edge carries source passage, extractor version, and validation status; graph is never retrieval truth |

### Trade-offs not made explicit

| Decision | Hidden cost | V2 decision |
|---|---|---|
| Files as source of truth | Cross-file joins and identity changes require a manifest | Accept; keep manifest deterministic and tested |
| SQLite FTS5 | Lexical misses on paraphrases | Accept for MVP; measure before dense retrieval |
| Fail closed | Availability loss from optional data defects | Apply only to mandatory transcript/index invariants |
| Full-corpus indexing | Build time, disk, and relevance debugging | Gate behind a 50-video slice |
| MCP | Excerpts can leave the machine through a cloud host | Stdio, read-only, bounded output, documented egress |
| Static exports | No live collaboration | Accept for solo/editorial MVP |
| Graph-lite in SQLite | Less convenient traversals than a graph DB | Accept until real multi-hop workload proves otherwise |

### Simpler alternatives considered

1. **Files plus grep only**
   - Gain: no database or index lifecycle.
   - Loss: weak ranking, filters, excerpts, and stable passage identities.
   - Verdict: too weak for three million segments.

2. **Files plus SQLite FTS5 plus MCP plus dossier files**
   - Gain: covers acquisition, retrieval, LLM access, persistence, and sharing with one local process.
   - Loss: semantic paraphrases and graph traversals wait for evidence.
   - Verdict: recommended.

3. **Vector/graph platform first**
   - Gain: rich semantic and relationship queries.
   - Loss: model lifecycle, alias resolution, vendor/runtime dependencies, false edges, migrations, and operational recovery before product validation.
   - Verdict: reject for MVP.

### Recommendations

1. Make the acceptance journey `channel → search → selected passages → Markdown dossier → article outline`.
2. Add a read-only stdio MCP immediately after the stable search service.
3. Separate storage into immutable corpus files, rebuildable search index, and authored dossier files.
4. Validate search on 50 videos and 20 reviewed queries before indexing the complete corpus.
5. Remove Qdrant, dense retrieval, generated questions, live web server, and graph DB from the active critical path.
6. Permit graph-lite only as derived SQLite tables after real multi-hop query evidence.

### 3 AM test

| Check | V1 | V2 requirement |
|---|---|---|
| Detection | Build/query metrics exist, editorial failure does not | End-to-end article scenarios plus build/search counters |
| Diagnosis | Many modules and adapters | One manifest, one index, one service, thin CLI/MCP adapters |
| Mitigation | Disable optional systems manually | CLI works without MCP; corpus and dossiers work without index |
| Recovery | Atomic index replacement is designed | Rebuild derived DB, restore prior generation, keep dossiers untouched |
| Prevention | Strong low-level tests | Add product-flow regression and bounded MCP contract tests |

### Verdict

**Architecture soundness:** 58% 🟠

The retrieval core is workable. The system boundary is wrong for the user's job and the optional architecture is ahead of demonstrated need.

**Top three actions:**

1. Put MCP and editorial dossiers into the MVP.
2. Prove one 50-video retrieval slice before full-corpus architecture hardens.
3. Demote graph, embeddings, web server, and Qdrant to evidence-triggered options.

**Key question before proceeding:**

Can the user produce a better source-backed article dossier from three real prompts without reading full transcripts manually? If not, the architecture has failed regardless of latency or index integrity.

---

## Plan Critique: yt-insights search roadmap v1

### Readiness

**Score:** 52% 🟠, needs major rework before implementation.

### What's solid

- Tests are planned RED-first and most low-level acceptance criteria are objective.
- Dependencies, worktree ownership, atomic writes, and negative boundaries receive more attention than in a typical early plan.

### Blockers

1. **The MVP is not the user journey.** Forty-one tasks deliver search infrastructure but no persistent editorial dossier or MCP.
   - Fix: redefine MVP around three article scenarios and add MCP plus dossier export.
2. **The scope is too large to validate.** Forty-two new files and eight implementation tracks create integration risk before one query is useful.
   - Fix: cap active MVP production units to corpus, search, MCP, and dossiers; defer the rest.
3. **The first relevance gate arrives too late.** Full contracts and modules precede real chunk/retrieval evidence.
   - Fix: insert a 50-video, 20-query gate before full-corpus implementation.

### Major concerns

1. **Evaluation is oversized too early.** Sixty to one hundred judgments and one hundred hostile queries block initial value.
   - Suggestion: start with 20 reviewed editorial queries and 20 hostile parser cases; expand only before semantic adoption or public release.
2. **Parallel sessions freeze synthetic interfaces.** SQLite, query service, and chunking can diverge while implemented against fakes.
   - Suggestion: integrate domain plus one vertical slice before parallel adapter work.
3. **Conditional plans still consume attention.** A 237-line Qdrant plan is active documentation for a need explicitly not demonstrated.
   - Suggestion: reduce Qdrant to a trigger in the decision register.
4. **The UI uses bespoke server code.** The plan creates an HTTP stack before proving a browser is needed.
   - Suggestion: static dossier HTML first; live UI only after five real dossier workflows expose selection friction.
5. **No acquisition-to-index state machine.** New, failed, renamed, and deleted videos are not joined into one operational status.
   - Suggestion: manifest each video through discovered, transcript available, analyzed, indexed, or failed states.
6. **Plans are untracked while GitHub issues cite them.** Remote collaborators cannot open the detailed source of truth.
   - Suggestion: commit only the approved consolidated planning paths before execution.

### Missing elements

- [ ] Three representative article jobs with expected sources and outputs.
- [ ] Read-only MCP contract, output bounds, and remote-LLM egress warning.
- [ ] Dossier data model, deterministic export, and preservation across index rebuilds.
- [ ] Optional-enrichment quarantine policy.
- [ ] Disk-space preflight for atomic full index builds.
- [ ] Concurrent read/rebuild regression.
- [ ] Acquisition resume and deleted-source reconciliation.
- [ ] Product-flow acceptance test from query to exported dossier.

### Scope creep check

**Essential MVP:**

- characterization-test baseline;
- deterministic corpus manifest and timestamped passages;
- SQLite FTS5 search with filters and source links;
- read-only stdio MCP;
- Markdown/JSON dossier files and static HTML export;
- 20 reviewed queries and three real article scenarios.

**Remove or defer:**

- generated timestamped questions;
- dense embeddings and RRF;
- Qdrant adapter/migration/runbook;
- dedicated graph database;
- graph extraction before real graph questions;
- live local HTTP server;
- hosted API, auth, Chrome extension, multi-user collaboration.

### Top three actions before starting

1. Replace the eight-track roadmap with one gated vertical-slice plan.
2. Define storage ownership and the MCP/dossier contracts.
3. Commit the approved consolidated plan, then execute only the baseline and 50-video slice.

