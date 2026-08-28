# Test Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the existing characterization-test branch without losing or absorbing unrelated local work.

**Architecture:** This plan changes no product behavior. It establishes a trusted regression baseline before search modules are added and keeps integration isolated from the dirty primary worktree.

**Tech Stack:** Git worktrees, Python 3.11+, pytest, Click test runner

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Preserve the modified `.claude/skills/yt-add-channel.md`, `CLAUDE.md`, and `runbook/run-channel.sh` files.
- Preserve the untracked `batches/` directory and `scripts/build_speakers.py` file.
- Never use `git add .`, `git add -A`, `git reset --hard`, or an unscoped restore.
- The existing test commits are `adc04d0`, `f37895c`, and `4d36790` in that order.
- The plan is complete only when the full test suite passes from a fresh isolated worktree.

---

### Task 1: Record the integration boundary

**Files:**
- Read: `.git/`
- Read: primary worktree status
- Create: none

**Interfaces:**
- Consumes: branch `codex/yt-insights-pr0-tests` and current `main`
- Produces: a verified merge-base and an explicit list of protected local paths

- [ ] **Step 1: Inspect the primary worktree**

Run:

```bash
rtk git status --short --branch
```

Expected: the five protected paths from the global constraints are present and no plan execution has modified them.

- [ ] **Step 2: Verify the test branch still starts at current main**

Run:

```bash
rtk git merge-base main codex/yt-insights-pr0-tests
rtk git rev-parse main
```

Expected: both commands print the same commit. If they differ, stop and rebase in an isolated worktree before continuing.

- [ ] **Step 3: Verify the exact commits being integrated**

Run:

```bash
rtk git log --oneline --reverse main..codex/yt-insights-pr0-tests
```

Expected:

```text
adc04d0 test(yt-insights): add characterization test foundation
f37895c test(yt-insights): reject unexpected cache LLM streams
4d36790 test(yt-insights): harden external boundary coverage
```

### Task 2: Verify the branch before integration

**Files:**
- Test: `tests/conftest.py`
- Test: `tests/test_analyzer.py`
- Test: `tests/test_cli_smoke.py`
- Test: `tests/test_config.py`
- Test: `tests/test_downloader.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Consumes: the isolated worktree for `codex/yt-insights-pr0-tests`
- Produces: fresh evidence that the existing 14 tests pass

- [ ] **Step 1: Enter the isolated test worktree**

Run:

```bash
cd /private/tmp/yt-insights-pr0-tests
```

Expected: `git branch --show-current` reports `codex/yt-insights-pr0-tests`.

- [ ] **Step 2: Run the complete suite**

Run:

```bash
rtk .venv/bin/pytest -q
```

Expected: `14 passed` and exit code `0`.

- [ ] **Step 3: Inspect the branch diff boundary**

Run:

```bash
rtk git diff --stat main...HEAD
rtk git diff --check main...HEAD
```

Expected: only test infrastructure and test files are introduced; `git diff --check` exits `0`.

### Task 3: Integrate without touching the primary dirty worktree

**Files:**
- Modify: Git history only
- Test: complete `tests/` directory

**Interfaces:**
- Consumes: the verified test branch
- Produces: an integration branch containing main plus the three test commits

- [ ] **Step 1: Create a clean integration worktree**

Use the `superpowers:using-git-worktrees` skill. Create branch `codex/search-baseline` from `main` in a new isolated worktree outside the primary checkout.

- [ ] **Step 2: Fast-forward the integration branch**

Run from the integration worktree:

```bash
rtk git merge --ff-only codex/yt-insights-pr0-tests
```

Expected: the branch advances through `4d36790` without a merge commit.

- [ ] **Step 3: Run the full test suite again**

Run:

```bash
rtk .venv/bin/pytest -q
```

Expected: `14 passed` and exit code `0`.

- [ ] **Step 4: Confirm protected files remain untouched**

Run in the primary worktree:

```bash
rtk git status --short --branch
```

Expected: the protected modifications remain exactly as recorded in Task 1.

- [ ] **Step 5: Record the integration result**

No new commit is required if the fast-forward points to `4d36790`. Update `plans/README.md` in a separate documentation commit after the branch is integrated.

## Acceptance gate

- The isolated integration branch contains all three test commits.
- The full suite reports `14 passed` after integration.
- No protected primary-worktree path changed.
- Search implementation sessions branch from the verified baseline, not from the dirty primary checkout.
