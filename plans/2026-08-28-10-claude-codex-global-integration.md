# Claude Code and Codex Global Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install one reviewed yt-insights workflow globally so Claude Code and Codex can search, acquire and export YouTube material from any project.

**Architecture:** Three portable skills call the packaged CLI or the read-only MCP. Claude Code and Codex receive native agent adapters. Explicit skill or agent invocation is the supported contract for this release because the global BM25 router candidate failed its promotion gate. The current immutable global configuration repository builds the candidate, binds global preimages to approval digests and provides rollback.

**Tech Stack:** Agent Skills, Claude Code Markdown agents, Codex TOML agents, MCP stdio, Node.js 22 global-config builder, JSON/TOML host configuration, pytest and Node test runner

**Spec:** `plans/specs/AGENT-PLATFORM.md`

## Global Constraints

- Start after required Tasks 1, 2, 4, 5, 6, 6.5 and 7 in `plans/2026-08-28-09-agent-ready-runtime.md` pass the runtime gate. Optional Task D1 is not a dependency.
- Treat `/Users/florianbruniaux/Sites/perso/yt-insights` as the runtime source repository and `/Users/florianbruniaux/.config/ai-agents` as the global configuration repository.
- Do not overwrite the existing dirty changes in `.claude/skills/yt-add-channel.md`, `CLAUDE.md` or `runbook/run-channel.sh`.
- Do not add a second global `UserPromptSubmit` hook.
- Do not grant global Bash, network, filesystem or cookie permissions.
- The research agents are read-only. Acquisition runs in the main session under normal host approvals.
- No wheel, user runtime config or global agent configuration changes before its exact digest-bound approval string.
- Build MCP candidates in an isolated fake home and redact values before presenting the diff.
- Use full absolute paths for local MCP executables and database files.
- The installed CLI and both MCP clients must resolve the same absolute `data_root` from `~/.config/yt-insights/config.toml`.
- Verify new sessions after installation. An existing session is not evidence that global assets load.

---

### Task 1: Author three canonical cross-host skills

**Files:**
- Create: `.agents/skills/youtube-acquire/SKILL.md`
- Create: `.agents/skills/youtube-acquire/agents/openai.yaml`
- Create: `.agents/skills/youtube-research/SKILL.md`
- Create: `.agents/skills/youtube-research/agents/openai.yaml`
- Create: `.agents/skills/youtube-export/SKILL.md`
- Create: `.agents/skills/youtube-export/agents/openai.yaml`
- Create: `tests/test_agent_assets.py`

**Interfaces:**
- Consumes: `yt-insights doctor`, `acquire`, `export` and MCP tools from Plan 09
- Produces: three portable Agent Skills; explicit invocation is the supported contract

- [x] **Step 1: Write failing structural tests**

```python
SKILLS = ("youtube-acquire", "youtube-research", "youtube-export")

@pytest.mark.parametrize("name", SKILLS)
def test_agent_skill_has_portable_entrypoint(name: str) -> None:
    root = REPOSITORY_ROOT / ".agents" / "skills" / name
    frontmatter, body = parse_skill(root / "SKILL.md")

    assert frontmatter["name"] == name
    assert 20 <= len(frontmatter["description"]) <= 500
    assert "/Users/" not in body
    assert "yt-dlp " not in body
    assert (root / "agents" / "openai.yaml").is_file()
```

Reject `echo $...KEY`, `cat ~/.claude`, `cat ~/.codex`, raw SQL and generic permission bypass instructions.

- [x] **Step 2: Verify the skills are absent**

```bash
rtk pytest tests/test_agent_assets.py -q
```

- [x] **Step 3: Write `youtube-acquire` with this routing contract**

```yaml
---
name: youtube-acquire
description: Acquire a YouTube video, playlist, channel, or transcript into the local yt-insights corpus. Use for requests such as recuperer une video, ajouter une chaine, download a transcript, or ingest a YouTube playlist. Do not use for code changes to a video player, YouTube SEO, or read-only corpus search.
---
```

The body must require this sequence:

1. run `yt-insights doctor --json`;
2. run `yt-insights acquire SOURCE --dry-run --json` with any requested filters;
3. present count, target, language and analysis mode;
4. for a video, execute the explicit request;
5. for a channel, playlist or batch, wait for confirmation, then repeat with `--yes`;
6. report selected, ready, failed and output paths.

It must not fall back to raw `yt-dlp`. It may suggest explicit `--cookies-from-browser chrome` only after the CLI reports a relevant failure.

- [x] **Step 4: Write `youtube-research` with this routing contract**

```yaml
---
name: youtube-research
description: Search and compare the local yt-insights YouTube corpus with source-backed passages, timestamps, channels, and video metadata. Use for trouve dans mon corpus, cherche cette citation, compare ces chaines, or find the source video. Do not use to download videos or modify the corpus.
---
```

The body must prefer MCP in this order: `list_corpora`, `search_videos`, `search_passages`, `get_passage`. It returns title, channel, timestamped URL and a bounded excerpt. It states when the index lacks evidence instead of inventing a match.

- [x] **Step 5: Write `youtube-export` with this routing contract**

```yaml
---
name: youtube-export
description: Export an existing yt-insights video transcript as Markdown, plain text, or VTT for article research and source archives. Use for exporte ce transcript, prepare la matiere de cette video, or save this video as Markdown. Do not use to search or download missing videos.
---
```

The body checks availability through the MCP or `catalog search`, asks for a language only when several exist, runs `yt-insights export video`, and returns the output path plus source hash.

- [x] **Step 6: Add OpenAI UI metadata and MCP dependency**

Each `agents/openai.yaml` contains a distinct display name and prompt. `youtube-research` declares the `yt-insights` MCP dependency. Host metadata may keep `allow_implicit_invocation` at its default `true`, but this release does not rely on or claim validated implicit routing.

- [x] **Step 7: Validate with the bundled skill validator**

```bash
python3 /Users/florianbruniaux/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/youtube-acquire
python3 /Users/florianbruniaux/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/youtube-research
python3 /Users/florianbruniaux/.codex/skills/.system/skill-creator/scripts/quick_validate.py .agents/skills/youtube-export
rtk pytest tests/test_agent_assets.py -q
```

- [x] **Step 8: Commit the canonical skills**

```bash
rtk git add .agents/skills/youtube-acquire .agents/skills/youtube-research .agents/skills/youtube-export tests/test_agent_assets.py
rtk git commit -m "feat: add portable YouTube corpus skills"
```

### Task 2: Add native read-only researcher agents

**Files:**
- Create: `.claude/agents/youtube-corpus-researcher.md`
- Create: `.codex/agents/youtube-corpus-researcher.toml`
- Modify: `tests/test_agent_assets.py`

**Interfaces:**
- Consumes: `youtube-research` and MCP server `yt-insights`
- Produces: one Claude Code subagent and one Codex custom agent named `youtube_corpus_researcher`

- [x] **Step 1: Add failing agent-schema tests**

```python
def test_codex_researcher_is_read_only() -> None:
    agent = tomllib.loads(CODEX_AGENT.read_text(encoding="utf-8"))
    assert agent["name"] == "youtube_corpus_researcher"
    assert agent["sandbox_mode"] == "read-only"
    assert "acquire" not in agent["developer_instructions"].lower()

def test_claude_researcher_preloads_only_research_skill() -> None:
    frontmatter, _ = parse_agent(CLAUDE_AGENT)
    assert frontmatter["skills"] == ["youtube-research"]
    assert frontmatter["mcpServers"] == ["yt-insights"]
    assert frontmatter["permissionMode"] == "plan"
```

- [x] **Step 2: Verify failures**

```bash
rtk pytest tests/test_agent_assets.py -q
```

- [x] **Step 3: Implement the Claude Code agent**

Use `name: youtube-corpus-researcher`, `model: inherit`, `permissionMode: plan`, `skills: [youtube-research]`, `mcpServers: [yt-insights]` and tools limited to `Read`, `Grep`, `Glob` plus the configured MCP. Its output contract requires claims, passages, timestamped URLs, coverage limits and unresolved questions.

- [x] **Step 4: Implement the Codex custom agent**

```toml
name = "youtube_corpus_researcher"
description = "Read-only researcher for source-backed searches across the local yt-insights corpus."
sandbox_mode = "read-only"
developer_instructions = """
Use the youtube-research skill and the yt-insights MCP. Return source-backed passages with channel, title, timestamped URL, and the query limits. Do not acquire videos, write exports, modify indexes, or infer evidence that the corpus did not return.
"""
```

Do not pin a model. The agent inherits the parent unless the user explicitly chooses another model.

- [x] **Step 5: Run and commit**

```bash
rtk pytest tests/test_agent_assets.py -q
rtk git add .claude/agents/youtube-corpus-researcher.md .codex/agents/youtube-corpus-researcher.toml tests/test_agent_assets.py
rtk git commit -m "feat: add Claude and Codex corpus researchers"
```

### Task 3: Keep legacy Claude workflows available without implicit collisions

**Files:**
- Modify: `.claude/skills/yt-add-channel.md`
- Modify: `.claude/skills/yt-get-transcript.md`
- Modify: `.claude/skills/yt-get-insights.md`
- Modify: `.claude/skills/yt-get-shorts.md`
- Modify: `.claude/skills/yt-run-pipeline.md`
- Modify: `tests/test_agent_assets.py`

**Interfaces:**
- Consumes: five legacy project skills
- Produces: explicit-only compatibility commands while the new skills own implicit routing

- [x] **Step 1: Capture exact preimages before editing**

```bash
rtk git diff -- .claude/skills/yt-add-channel.md .claude/skills/yt-get-transcript.md .claude/skills/yt-get-insights.md .claude/skills/yt-get-shorts.md .claude/skills/yt-run-pipeline.md
shasum -a 256 .claude/skills/yt-add-channel.md .claude/skills/yt-get-transcript.md .claude/skills/yt-get-insights.md .claude/skills/yt-get-shorts.md .claude/skills/yt-run-pipeline.md
```

Store the hashes in the execution notes. If a preimage changes before editing, stop this task and recompute the intended patch.

- [x] **Step 2: Add a failing collision test**

Require each legacy frontmatter to contain `disable-model-invocation: true`. Require its body to point to the replacement skill where relevant. Do not require any other body change.

- [x] **Step 3: Add only the explicit-only field and migration note**

Preserve every current line of the modified `yt-add-channel.md` body. Add:

```yaml
disable-model-invocation: true
```

Do the same for the four clean legacy skills. Add one short first paragraph naming the replacement command without deleting the legacy procedure.

- [x] **Step 4: Verify the scoped diff**

```bash
rtk pytest tests/test_agent_assets.py -q
rtk git diff -- .claude/skills/yt-add-channel.md .claude/skills/yt-get-transcript.md .claude/skills/yt-get-insights.md .claude/skills/yt-get-shorts.md .claude/skills/yt-run-pipeline.md
rtk git diff --check
```

- [x] **Step 5: Commit only the named files**

```bash
rtk git add .claude/skills/yt-add-channel.md .claude/skills/yt-get-transcript.md .claude/skills/yt-get-insights.md .claude/skills/yt-get-shorts.md .claude/skills/yt-run-pipeline.md tests/test_agent_assets.py
rtk git commit -m "chore: retire legacy implicit YouTube skill routing"
```

### Task 4: Build a behavioral routing corpus

**Files:**
- Create: `tests/fixtures/agent-routing.json`
- Create: `scripts/check_agent_routing_fixture.py`
- Modify: `tests/test_agent_assets.py`

**Interfaces:**
- Consumes: 45 French and English prompts with expected skill or `none`
- Produces: deterministic fixture validation and host-level acceptance prompts

- [x] **Step 1: Add the 30 positive cases**

Include ten prompts for each skill. Cover URLs, video IDs, channel handles, transcript wording, article-source exports and corpus queries. Use realistic prompts such as:

```json
{"prompt": "Trouve dans mon corpus les passages sur les hooks Claude Code", "expected": "youtube-research"}
{"prompt": "Récupère le transcript de https://youtu.be/nfupYzLjFGc", "expected": "youtube-acquire"}
{"prompt": "Exporte nfupYzLjFGc en Markdown pour mon article", "expected": "youtube-export"}
```

- [x] **Step 2: Add the 15 negative cases**

Cover code for a YouTube player, SEO, video editing, writing a title, discussing a watched video, generic web scraping and non-YouTube corpora.

```json
{"prompt": "Corrige le composant React de mon lecteur YouTube", "expected": "none"}
{"prompt": "Optimise le SEO de ma prochaine vidéo", "expected": "none"}
```

- [x] **Step 3: Validate fixture integrity**

The script rejects duplicate prompts, unknown expected values, fewer than ten positives per skill, fewer than fifteen negatives, and non-UTF-8 text.

- [x] **Step 4: Run and commit**

```bash
python3 scripts/check_agent_routing_fixture.py tests/fixtures/agent-routing.json
rtk pytest tests/test_agent_assets.py -q
rtk git add tests/fixtures/agent-routing.json scripts/check_agent_routing_fixture.py tests/test_agent_assets.py
rtk git commit -m "test: define YouTube agent routing cases"
```

### Task 5: Vendor the skills and agents into the immutable global release source

**Repository:** `/Users/florianbruniaux/.config/ai-agents`

**Files:**
- Create: `src/skills/common/youtube-acquire/`
- Create: `src/skills/common/youtube-research/`
- Create: `src/skills/common/youtube-export/`
- Create: `src/agents/claude/youtube-corpus-researcher.md`
- Create: `src/agents/codex/youtube-corpus-researcher.toml`
- Create: `src/upstreams/yt-insights.json`
- Modify: `scripts/render.mjs`
- Modify: `scripts/check.mjs`
- Modify: `tests/render.test.mjs`
- Create: `tests/yt-insights-upstream.test.mjs`

**Interfaces:**
- Consumes: exact tracked files and Git SHA from the yt-insights repository
- Produces: release-contained skill and agent projections with provenance hashes

- [x] **Step 1: Verify both repositories before copying**

```bash
rtk git status --short --branch
rtk git rev-parse HEAD
```

Run once in each repository. The yt-insights asset files must be committed. The global-config worktree may contain unrelated changes, but the named target paths must be clean.

- [x] **Step 2: Add failing release projection tests**

Assert a rendered release contains:

```text
skills/projections/claude/youtube-acquire/
skills/projections/claude/youtube-research/
skills/projections/claude/youtube-export/
skills/projections/codex/youtube-acquire/
skills/projections/codex/youtube-research/
skills/projections/codex/youtube-export/
agents/claude/youtube-corpus-researcher.md
agents/codex/youtube-corpus-researcher.toml
```

- [x] **Step 3: Vendor exact asset bytes and record provenance**

`src/upstreams/yt-insights.json` records repository path, source Git SHA and SHA256 for every imported file. The test recomputes hashes when the source checkout is available and otherwise validates the vendored hashes internally.

- [x] **Step 4: Extend release rendering**

`scripts/render.mjs` copies the two agent projections into the immutable release and includes their hashes in `artifact-manifest.json`. `scripts/check.mjs` rejects missing, malformed or extra agent files.

- [x] **Step 5: Run global configuration tests**

```bash
rtk npm test
node scripts/render.mjs --source "$PWD/src" --hooks "$PWD/hooks" --output /private/tmp/ai-agents-yt-releases --source-commit "$(git rev-parse HEAD)"
node scripts/check.mjs --release /private/tmp/ai-agents-yt-releases/RELEASE_ID --home /Users/florianbruniaux
```

Replace `RELEASE_ID` with the exact directory printed by the renderer, not a guessed value.

- [x] **Step 6: Commit the inert release source**

```bash
rtk git add src/skills/common/youtube-acquire src/skills/common/youtube-research src/skills/common/youtube-export src/agents src/upstreams/yt-insights.json scripts/render.mjs scripts/check.mjs tests/render.test.mjs tests/yt-insights-upstream.test.mjs
rtk git commit -m "feat: package yt-insights agent integrations"
```

### Task 6: Add digest-bound runtime and agent integration transactions

**Repository:** `/Users/florianbruniaux/.config/ai-agents`

**Files:**
- Modify: `scripts/inventory.mjs`
- Modify: `scripts/prepare-approval.mjs`
- Modify: `scripts/install.mjs`
- Create: `lib/mcp-candidate.mjs`
- Modify: `tests/prepare-approval.test.mjs`
- Modify: `tests/install.test.mjs`
- Create: `tests/mcp-candidate.test.mjs`
- Create: `tests/runtime-install.test.mjs`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified wheel, resolved uv tool paths, absolute data root, release agents, two database paths and live global preimages
- Produces: approval kinds `yt-runtime` and `yt-integrations` with confirmations `GO INSTALL YT RUNTIME <digest>` and `GO INSTALL YT INTEGRATIONS <digest>`

- [x] **Step 1: Add failing allowlist and stale-preimage tests**

The `yt-runtime` transaction may touch only the exact uv tool directory resolved
during preparation, the two exact uv bin entrypoints and the user runtime config:

```text
<resolved-uv-tool-dir>/yt-insights/
<resolved-uv-bin-dir>/yt-insights
<resolved-uv-bin-dir>/yt-insights-mcp
~/.config/yt-insights/config.toml
```

The approval records the prior tool tree manifest or absence, prior entrypoint
hashes or absence, the wheel SHA-256, candidate config hash and absolute
`data_root`. Tests use fake `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR` and `HOME` roots.
The config file is atomically written with mode `600`.

The `yt-integrations` transaction may touch only:

```text
~/.claude/agents/youtube-corpus-researcher.md
~/.codex/agents/youtube-corpus-researcher.toml
~/.claude.json
~/.codex/config.toml
```

Reject any extra operation, target outside the resolved narrow roots, relative
target, different agent filename, MCP name other than `yt-insights`, changed
preimage, wheel hash mismatch, wrong digest or generic `GO`.

- [x] **Step 2: Build MCP candidates with structured parsers**

Copy the live Claude JSON and Codex TOML into a mode-`700` candidate directory.
Use JSON and TOML parsers to replace only the semantic `yt-insights` MCP entry,
then round-trip parse both candidates. Do not override `HOME` or `CODEX_HOME`.

Resolve the MCP entrypoint with `uv tool dir --bin`; do not hardcode a guessed
path. Derive both database paths from the same `data_root` written by
`yt-runtime`. The candidate contract is equivalent to these native commands,
which are run only after approved installation:

```bash
claude mcp get yt-insights
codex mcp get yt-insights
```

Before installation, validate the standalone Claude MCP candidate with
`claude --bare --strict-mcp-config --mcp-config <candidate>`. Validate the Codex
candidate through the TOML schema tests in `mcp-candidate.test.mjs`; final Codex
parsing is a mandatory fresh-session gate after installation.

- [x] **Step 3: Produce an exact redacted diff**

The candidate builder prints operation IDs, target paths, preimage hashes, candidate hashes and semantic MCP fields. It never prints unrelated JSON values, TOML values, headers, tokens or environment secrets.

- [x] **Step 4: Implement runtime install, atomic config writes and rollback**

Before each write, compare the live preimage with the approved state. Snapshot
the exact previous uv tool tree and entrypoints before invoking `uv tool
install` with the approved wheel. Snapshot modes are `700` for directories and
`600` for files. Copy candidate config files atomically. If any operation
fails, restore already changed targets and return exit code `5`; return `6`
when restoration cannot be proved.

- [x] **Step 5: Run hostile transaction tests**

```bash
rtk npm test
```

The test suite must include forged approvals, symlink targets, concurrent
config edits, missing agent sources, wheel hash mismatch, partial uv tool
replacement, partial config write failure and rollback after a third-party
post-install edit.

- [x] **Step 6: Commit the transaction code**

```bash
rtk git add scripts/inventory.mjs scripts/prepare-approval.mjs scripts/install.mjs lib/mcp-candidate.mjs tests/prepare-approval.test.mjs tests/install.test.mjs tests/mcp-candidate.test.mjs tests/runtime-install.test.mjs README.md
rtk git commit -m "feat: add yt-insights runtime and integration transactions"
```

### Task 7: Calibrate the existing global skill router

**Repository:** `/Users/florianbruniaux/.config/ai-agents`

**Files:**
- Modify: the existing skill-router evaluation fixture selected by its current test suite
- Modify: router thresholds only if the 45-prompt corpus demonstrates a miss
- Test: existing skill-router Node tests and benchmark

**Interfaces:**
- Consumes: `tests/fixtures/agent-routing.json` from yt-insights
- Produces: one route or no route, without a new hook handler

- [x] **Step 1: Import the 45 prompts into the router evaluation**

Preserve the expected labels. Map `none` to the router's forbidden-hit contract.

- [x] **Step 2: Run the router before changing thresholds**

```bash
node /Users/florianbruniaux/.codex/hooks/skill-router/routing/benchmark.js
```

Record precision, recall, F1, forbidden hits and warm p95.

Résultat du 2026-08-28 : `FAIL`, 20/30 routes positives correctes,
17 confusions inter-skills, 0/15 activations interdites, p95 0,709 ms. Aucun
seuil global, hook ou fichier live n'a été modifié.

- [x] **Step 3: Change only skill descriptions or per-skill thresholds supported by misses**

Do not lower a global threshold to rescue one YouTube prompt. Add contrastive negatives for player development, SEO and video editing when they cause false positives.

Résultat final : quatre familles simples ont été évaluées sur un holdout
disjoint, sans optimisation brute. L'exclusivité top-1 conserve 30/30 positifs
et supprime les trois confusions, mais déclenche encore 5/15 requêtes
interdites. Les seuils par skill, marges et ancres d'intention perdent des
positifs ou conservent des faux positifs. Aucun changement de production n'est
retenu.

- [x] **Step 4: Enforce promotion thresholds**

```text
positive routes: at least 27/30
negative false activations: 0/15
warm p95: <= 10 ms
existing forbidden hits: 0
```

Gate final : `FAIL`. Le routage implicite BM25 des trois skills est abandonné
pour ce lot au profit de l'invocation explicite et des agents natifs.

- [x] **Step 5: Commit calibration data**

Stage only the evaluation, description and threshold files that changed. Do not modify the hook registration.

```bash
rtk git commit -m "test: calibrate yt-insights skill routing"
```

Aucun commit de calibration n'a été promu. Les commits expérimentaux restent
dans les clones inertes et sont explicitement non promouvables.

### Task 7.5: Keep the local Claude hook because routing failed

**Repository:** `/Users/florianbruniaux/Sites/perso/yt-insights`

**Files:**
- Preserve unchanged: `.claude/settings.json`
- Preserve unchanged: `tests/test_agent_assets.py`
- Preserve unchanged: `.claude/hooks/yt-channel-router.sh`

- [x] Record the decision as `NOT APPLICABLE` for this release because Task 7 failed.
- [x] Preserve `.claude/settings.json` and `.claude/hooks/yt-channel-router.sh` unchanged.
- [x] Keep explicit skill and agent invocation as the supported path.
- [ ] Reopen hook retirement only if a future disjoint holdout passes 27/30 positives, 0/15 negatives and the existing router has zero forbidden hits.
- [ ] If reopened, hash both files before editing and validate the 45-prompt corpus in a fresh Claude Code project session.
- [ ] Commit only the settings file and focused test. If routing regresses, restore the exact preimage and stop.

### Task 8: Build candidates and stop at the approval gate

**Repositories:** both repositories

**Files:** no live global writes

**Interfaces:**
- Consumes: committed runtime wheel, committed global config candidate and current live hashes
- Produces: three approval artifacts and their exact confirmation strings

- [x] **Step 1: Install the wheel into an isolated uv tool directory**

```bash
UV_TOOL_DIR=/private/tmp/yt-insights-tools UV_TOOL_BIN_DIR=/private/tmp/yt-insights-bin \
  uv tool install --from /ABSOLUTE/WHEEL/PATH 'yt-insights[mcp]'
/private/tmp/yt-insights-bin/yt-insights doctor --json
```

This proves the package before any user-level tool replacement.

Résultat du 2026-08-28 : wheel SHA-256
`3c69f3379951ee226d8d514b24cd9229183a1cf914dcb6a4427cfb5e99bc73ee`,
37 dépendances et deux exécutables installés sous `/private/tmp`. Le diagnostic
sur un corpus temporaire vide retourne seulement les avertissements attendus.
Le corpus réel reste bloqué car son reçu d'index est invalide et son catalogue
n'est pas construit.

- [x] **Step 2: Re-inventory live global preimages**

Record hashes for the resolved uv tool tree and two bin entrypoints,
`~/.config/yt-insights/config.toml`, `~/.claude/CLAUDE.md`,
`~/.claude/settings.json`, `~/.claude.json`,
`~/.claude/agents/youtube-corpus-researcher.md` if present,
`~/.codex/AGENTS.md`, `~/.codex/config.toml`, `~/.codex/hooks.json` and
`~/.codex/agents/youtube-corpus-researcher.toml` if present.

- [x] **Step 3: Render and check the immutable release ten times**

All ten release IDs must match. `npm test` and `scripts/check.mjs` must pass.

Résultat du 29 août 2026: 144 tests sur 144 passent. Dix rendus produits à
partir de `62aa9ca053c9bc7c03564ffb08864d5d02f8f8b6` convergent vers la release
`60cbcac1db3728e861560cd945e614bca0b8b0e8404acadddc8d57e1b46be1eb`.
Le check de release retourne `issues: []`.

- [x] **Step 4: Prepare the runtime approval**

Bind the exact wheel SHA-256, resolved tool paths, prior tree manifest, two
entrypoints and candidate user TOML to `GO INSTALL YT RUNTIME <digest>`.
The TOML contains one absolute `data_root` and no credential value.

Artefact préparé dans `/private/tmp`, digest
`a788c8b72b97959512d512afc536a00c91592261e44ac44d58516679731f5eb4`.
Il ne doit pas être installé tant que le corpus réel ne passe pas le diagnostic.

- [x] **Step 5: Prepare the shared release approval**

Use the existing `shared` transaction to update instructions, project index and skill projections. Present its redacted diff and exact `GO INSTALL SHARED <digest>` string.

Le candidat `62aa9ca` traite la préimage absente et les courses concurrentes
sans suppression récursive de la cible live. Le digest partagé préparé sur les
préimages du 29 août 2026 est
`cbd58f0a09d95e9ba676b1f9271f9fec9c2966ac2ef8dc9223d45167ef52f296`.
Il ne doit pas être installé avant l'intégration approuvée du candidat source,
puis une nouvelle vérification des préimages.

- [ ] **Step 6: Prepare the yt integration approval**

Use the new `yt-integrations` transaction to prepare the two agents and two MCP configs. Present its redacted diff and exact `GO INSTALL YT INTEGRATIONS <digest>` string.

Blocage attendu : cette préparation exige l'exécutable live
`yt-insights-mcp`, créé seulement après l'installation runtime approuvée.

- [ ] **Step 7: Stop**

Do not install any candidate in the same turn that presents the digests. Wait
for all three exact confirmation strings.

### Task 9: Install and verify after exact approval

**Files:** approved global targets only

**Interfaces:**
- Consumes: three exact current approval artifacts and confirmation strings
- Produces: installed wheel, runtime config, skills, agents, MCP configuration and three rollback journals

- [ ] **Step 1: Recheck every live preimage**

If any hash differs from the approval artifact, abort with exit code `3` and prepare a new digest. Do not ask to reuse the old approval.

- [ ] **Step 2: Install the runtime transaction**

Run `scripts/install.mjs install` with `GO INSTALL YT RUNTIME <digest>`. Use
the exact wheel SHA-256 tested in Task 8. Record the runtime journal,
`uv tool list`, binary paths, `yt-insights --version` and the redacted output of
`yt-insights doctor --json`.

- [ ] **Step 3: Install the shared release**

Run `scripts/install.mjs install` with `GO INSTALL SHARED <digest>`. Report the release ID and journal path immediately.

- [ ] **Step 4: Install agents and MCP**

Run `scripts/install.mjs install` with `GO INSTALL YT INTEGRATIONS <digest>`. Report the journal path immediately.

- [ ] **Step 5: Verify host discovery**

```bash
claude mcp get yt-insights
codex mcp get yt-insights
```

Start a new Claude Code session and a new Codex session. Confirm the three skills and the native researcher agent appear in each supported selector.

- [ ] **Step 6: Run five parity queries**

For five fixed queries, record the first five passage IDs from Claude Code and Codex. The ordered IDs must match because both clients use the same MCP database.

Run one CLI search from the yt-insights checkout and the same search from an
unrelated temporary directory without setting an environment variable. Both
must report the same absolute databases and ordered passage IDs.

- [ ] **Step 7: Run safety prompts**

- A channel request must stop after preview until confirmation.
- A single-video transcript request may proceed.
- A corpus search must use MCP and perform no write.
- An export must use the CLI and name its output.
- A React YouTube player request must not invoke any yt-insights skill.

Invoke each named researcher agent in a fresh scratch workspace with three
hostile requests: create a sentinel file, acquire a video and export a
transcript. Record the host tool trace and assert refusal, no sentinel file, no
new corpus artifact and unchanged catalog/search database hashes. Static TOML
or frontmatter validation alone is not sufficient evidence of read-only mode.

- [ ] **Step 8: Verify Codex hook state without changing it**

Open `/hooks`, confirm no new YouTube handler exists, and verify the existing router remains trusted at its expected hash.

- [ ] **Step 9: Roll back on any failed mandatory gate**

Use the exact journals in reverse transaction order: `yt-integrations`,
`shared`, then `yt-runtime`. After rollback, recompute hashes and confirm they
match the pre-install inventory. A failed fresh-session behavior check is a
rollback trigger, not a documentation-only warning.

## Global integration acceptance gate

- The three skills are discoverable in fresh Claude Code and Codex sessions.
- The Claude and Codex researcher agents are present and read-only.
- Both clients connect to the same absolute catalog and search databases.
- The global CLI resolves the same absolute corpus from two unrelated current directories.
- Five parity queries return the same ordered passage IDs.
- The failed 45-prompt routing evaluation is recorded, and no BM25 calibration candidate is promoted.
- Explicit invocation of each named skill and researcher agent works in fresh sessions.
- No second global YouTube hook is installed.
- The local Claude YouTube hook remains unchanged while the replacement routing gate is failed.
- Channel acquisition requires explicit confirmation.
- Global configuration diffs contain no secret values.
- Wrong or stale digests fail closed.
- The runtime, shared and integration transactions each have a tested rollback.
- Rollback restores every approved preimage hash.
