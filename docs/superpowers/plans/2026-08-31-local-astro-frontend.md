# Local Astro Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the accessible Astro interface for corpus search, sources, cumulative research sessions, queued work, and exports, then package its static output with Python.

**Architecture:** Astro pre-renders framework-free pages into the Python package. Small strict-TypeScript modules fetch the fixed `/api/v1` contract and render untrusted values through DOM nodes and `textContent`; the Python server rewrites `/research/<id>` to the static research workspace shell.

**Tech Stack:** Astro 7.2.9, TypeScript strict mode, CSS custom properties, pnpm, Vitest, Playwright smoke test

**Spec:** `docs/superpowers/specs/2026-08-31-local-astro-web-ui-design.md`

## Global Constraints

- All interface copy and prompts are English.
- Light mode is the default; theme preference is local to the browser.
- No React, Preact, Vue, remote font, analytics, external runtime script, or browser-side ranking.
- Never use `innerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, or string-built event handlers.
- Open only validated API-provided YouTube watch URLs with `noopener noreferrer`.
- Generated production assets live in `src/yt_insights/web/static/` and are committed.
- The installed application must run without Node.js.
- Every page is usable with a keyboard and respects `prefers-reduced-motion`.

---

## File map

| File group | Responsibility |
|---|---|
| `web/src/layouts/AppLayout.astro` | Shared document, navigation, skip link, bootstrap |
| `web/src/components/` | Semantic cards, forms, tables, notices, and research panels |
| `web/src/lib/api.ts` | Same-origin API client and fixed public errors |
| `web/src/lib/dom.ts` | Safe DOM creation and focus helpers |
| `web/src/lib/theme.ts` | Light/dark preference without flash |
| `web/src/lib/pages/` | One controller per page |
| `web/src/styles/` | Tokens, global accessibility, and responsive layouts |
| `web/tests/` | Pure helper and fixture-contract tests |
| `web/e2e/` | Browser smoke test against a fixture API |

### Task 1: Scaffold reproducible Astro output and the accessible shell

**Files:**
- Create: `web/package.json`
- Create: `web/pnpm-lock.yaml`
- Create: `web/astro.config.mjs`
- Create: `web/tsconfig.json`
- Create: `web/vitest.config.ts`
- Create: `web/src/layouts/AppLayout.astro`
- Create: `web/src/components/AppNav.astro`
- Create: `web/src/styles/tokens.css`
- Create: `web/src/styles/global.css`
- Create: `web/src/lib/theme.ts`
- Create: `web/src/pages/index.astro`
- Test: `web/tests/theme.test.ts`

**Interfaces:**
- Consumes: a same-origin `/api/v1/status` endpoint and bootstrap token
- Produces: static output at `../src/yt_insights/web/static`
- Produces: `applyStoredTheme(storage: Storage, root: HTMLElement) -> "light" | "dark"`

- [ ] **Step 1: Create the package manifest with exact scripts**

```json
{
  "private": true,
  "type": "module",
  "scripts": {
    "build": "astro build",
    "check": "astro check",
    "test": "vitest run",
    "test:e2e": "playwright test"
  },
  "dependencies": {"astro": "7.2.9"},
  "devDependencies": {
    "@astrojs/check": "0.9.10",
    "@playwright/test": "1.62.1",
    "happy-dom": "20.12.0",
    "typescript": "7.0.2",
    "vitest": "4.1.11"
  }
}
```

Generate and commit the pnpm lockfile. The implementation environment is
Node.js 24.16.0 with pnpm 11.23.0; dependency installation must fail rather
than silently substituting another version.

- [ ] **Step 2: Configure static output into the Python package**

```javascript
import { defineConfig } from "astro/config";

export default defineConfig({
  output: "static",
  outDir: "../src/yt_insights/web/static",
  build: { format: "directory" },
  vite: { build: { sourcemap: false } }
});
```

- [ ] **Step 3: Write the failing theme test**

```typescript
it("defaults to light and restores only known values", () => {
  const root = document.documentElement;
  expect(applyStoredTheme(fakeStorage(null), root)).toBe("light");
  expect(applyStoredTheme(fakeStorage("dark"), root)).toBe("dark");
  expect(applyStoredTheme(fakeStorage("system"), root)).toBe("light");
});
```

- [ ] **Step 4: Implement the shared shell**

Add a skip link, semantic header/main/footer, active navigation, theme button,
fixed metadata, no remote assets, visible focus states, 44 px pointer targets,
and reduced-motion overrides. Define light tokens first and dark overrides under
`[data-theme="dark"]`.

- [ ] **Step 5: Run the first frontend gate**

Run:

```bash
pnpm --dir web test
pnpm --dir web check
pnpm --dir web build
```

Expected: every command exits `0` and creates `src/yt_insights/web/static/index.html`.

- [ ] **Step 6: Commit the shell**

```bash
git add web src/yt_insights/web/static
git commit -m "feat(web): add accessible Astro application shell"
```

### Task 2: Implement the safe API client and reusable UI states

**Files:**
- Create: `web/src/lib/api.ts`
- Create: `web/src/lib/dom.ts`
- Create: `web/src/lib/types.ts`
- Create: `web/src/components/StatusNotice.astro`
- Create: `web/src/components/MetricCard.astro`
- Create: `web/src/components/EmptyState.astro`
- Test: `web/tests/api.test.ts`
- Test: `web/tests/dom.test.ts`

**Interfaces:**
- Consumes: fixed `/api/v1` JSON and bootstrap token
- Produces: `apiGet<T>(path: ApiPath, signal?: AbortSignal) -> Promise<T>`
- Produces: `apiPost<T>(path: ApiPath, body: unknown, signal?: AbortSignal) -> Promise<T>`
- Produces: `replaceChildren(target: Element, children: readonly Node[]) -> void`

- [ ] **Step 1: Write failing response and mutation-token tests**

Assert GET sends no token, POST sends `Content-Type: application/json` and the
bootstrap token, non-JSON responses become `unexpected_response`, `409` becomes
`stale_revision`, and raw server text is never exposed through the thrown
`PublicApiError`.

- [ ] **Step 2: Define strict public TypeScript shapes**

Define `StatusResponse`, `SearchResponse`, `SourcesResponse`,
`ResearchListResponse`, `ResearchResponse`, `JobResponse`, and `ExportsResponse`.
Use discriminated unions for job status and required user action. Do not use
`any`; parse unknown response envelopes before casting payload fields.

- [ ] **Step 3: Implement safe DOM helpers**

Every text node uses `document.createTextNode` or `textContent`. Link creation
accepts only URLs whose parsed protocol is `https:`, hostname is
`youtube.com` or `www.youtube.com`, and pathname is `/watch`.

- [ ] **Step 4: Run helper tests and static source scan**

Run:

```bash
pnpm --dir web test
rtk rg -n "innerHTML|insertAdjacentHTML|document\.write|\beval\(" web/src
```

Expected: tests pass and the scan returns no matches.

- [ ] **Step 5: Commit the shared client**

```bash
git add web/src/lib web/src/components web/tests
git commit -m "feat(web): add safe browser API client"
```

### Task 3: Build dashboard, search, and sources screens

**Files:**
- Modify: `web/src/pages/index.astro`
- Create: `web/src/pages/search.astro`
- Create: `web/src/pages/sources.astro`
- Create: `web/src/components/SearchForm.astro`
- Create: `web/src/components/PassageCard.astro`
- Create: `web/src/components/SourceTable.astro`
- Create: `web/src/components/SourceImportPanel.astro`
- Create: `web/src/lib/pages/dashboard.ts`
- Create: `web/src/lib/pages/search.ts`
- Create: `web/src/lib/pages/sources.ts`
- Test: `web/tests/search-page.test.ts`
- Test: `web/tests/sources-page.test.ts`

**Interfaces:**
- Consumes: `GET /status`, `/search`, `/sources`, `/research/sessions`, `/exports`
- Produces: dashboard health, global search, paginated source inventory, preview-confirmed source acquisition

- [ ] **Step 1: Write fixture-driven state tests**

Cover loading, ready, empty, invalid query, missing index, server unavailable,
aborted prior search, and page boundary states. Assert a successful search moves
focus to the result summary and every timestamp link is safe.

- [ ] **Step 2: Implement progressive dashboard loading**

Load status first, then recent sessions and exports in parallel. A failed
secondary panel must not hide the healthy corpus status. Add prominent actions
for `Search the corpus`, `Start research`, and `Add a YouTube source`.

- [ ] **Step 3: Implement search interactions**

Use a GET form enhanced with `AbortController`. Keep query, channel, language,
and limit in `URLSearchParams` so reload and back navigation preserve filters.
Render title, channel, language, excerpt, and timestamp link without injecting
HTML.

- [ ] **Step 4: Implement source inventory**

Render a responsive table on wide screens and labelled cards below 720 px.
Pagination uses bounded `limit` and `offset`; it never requests all sources.

- [ ] **Step 5: Implement preview-confirmed source acquisition**

Accept one YouTube video, playlist, or channel URL. Submit preview first and
render the safe plan projection with source kind, selected count, language,
exclusions, and bounded discovery errors. Never render `output_root` or another
path from the domain plan. Require a separate `Acquire these videos` action,
submit it as a job, and poll without automatically retrying failures.

- [ ] **Step 6: Run page tests, type checks, and build**

Run:

```bash
pnpm --dir web test
pnpm --dir web check
pnpm --dir web build
```

Expected: every command exits `0`.

- [ ] **Step 7: Commit the corpus screens**

```bash
git add web/src web/tests src/yt_insights/web/static
git commit -m "feat(web): add corpus dashboard and search"
```

### Task 4: Build research creation, workspace, jobs, and exports

**Files:**
- Create: `web/src/pages/research/new.astro`
- Create: `web/src/pages/research/workspace.astro`
- Create: `web/src/pages/exports.astro`
- Create: `web/src/components/EvidencePanel.astro`
- Create: `web/src/components/DecisionPanel.astro`
- Create: `web/src/components/CandidateList.astro`
- Create: `web/src/components/EventTimeline.astro`
- Create: `web/src/components/JobProgress.astro`
- Create: `web/src/lib/pages/research-new.ts`
- Create: `web/src/lib/pages/research-workspace.ts`
- Create: `web/src/lib/pages/exports.ts`
- Test: `web/tests/research-page.test.ts`

**Interfaces:**
- Consumes: all documented research, job, and export endpoints
- Produces: explicit sufficiency, candidate approval, acquisition, retry, and export flows

- [ ] **Step 1: Write the research state-machine view tests**

Assert `confirm_sufficiency_or_refresh` renders exactly two primary choices,
`approve_candidates_or_cancel` requires 1 to 5 checked IDs, discovery and
acquisition jobs disable duplicate submission, `409` reloads the snapshot and
shows `The session changed. Review the current evidence before deciding again.`,
and no failed job is automatically retried.

- [ ] **Step 2: Implement research creation**

Accept topic, newline-separated queries, comma-separated languages, and one of
`fast`, `standard`, `stable`, or `historical`. Generate an idempotency key with
`crypto.randomUUID()` where required. Redirect only to the validated session
identifier returned by the API.

- [ ] **Step 3: Implement the evidence workspace**

Read the session ID from `/research/<id>`, fetch the public snapshot, render
coverage and evidence in the main column, and render only the current required
decision in the secondary column. The timeline is a native `details` element.

- [ ] **Step 4: Implement polling with bounded backoff**

Poll the returned job at 500 ms, 1 s, then 2 s intervals, stop after 60 polls,
stop immediately when the page is hidden or aborted, and reload the durable
session after a terminal job. Never resubmit the original mutation.

- [ ] **Step 5: Implement exports list and export action**

Show bounded manifest projections. The server response controls whether an
export is valid; browser code never constructs or displays a filesystem path.

- [ ] **Step 6: Run the research frontend gate**

Run:

```bash
pnpm --dir web test
pnpm --dir web check
pnpm --dir web build
```

Expected: every command exits `0`.

- [ ] **Step 7: Commit the research experience**

```bash
git add web/src web/tests src/yt_insights/web/static
git commit -m "feat(web): add cumulative research workspace"
```

### Task 5: Add browser smoke coverage, reproducible build checks, and docs

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/local-ui.spec.ts`
- Create: `web/e2e/fixture-server.ts`
- Create: `scripts/verify_web_build.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `INSTALL.md`
- Modify: `ROADMAP.md`
- Modify: `docs/IMPLEMENTATION-STATUS.md`
- Modify: `CHANGELOG.md`
- Test: `tests/web/test_static_assets.py`
- Test: `tests/test_packaging.py`

**Interfaces:**
- Consumes: built static assets and fixed API fixtures
- Produces: reproducible frontend gate and documented local setup

- [ ] **Step 1: Add the fixture browser server**

Serve the built assets and deterministic JSON fixtures only on an ephemeral
loopback port. Record mutation calls in memory so tests can assert the exact
revision, ID list, and idempotency key without touching a real corpus.

- [ ] **Step 2: Write one end-to-end smoke path**

```typescript
test("searches, persists theme, and preserves the research approval gates", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Use dark theme" }).click();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("link", { name: "Search the corpus" }).click();
  await page.getByLabel("Search transcripts").fill("local inference");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByRole("heading", { name: /1 result/ })).toBeFocused();
  await page.goto("/research/session1234567890abcdef1234567890ab");
  await expect(page.getByRole("heading", { name: "Is this evidence sufficient?" })).toBeVisible();
});
```

- [ ] **Step 3: Add deterministic generated-asset verification**

`scripts/verify_web_build.py` snapshots the committed static tree hashes, runs
the documented pnpm build, snapshots again, and exits nonzero when hashes differ.
It prints relative paths only.

- [ ] **Step 4: Add static security tests**

Scan source and production assets for unsafe DOM APIs, source maps, remote
scripts, inline event attributes, absolute workspace paths, and accidental
secrets. Parse each generated HTML file and assert one main landmark, skip link,
language, title, viewport metadata, and local-only scripts.

- [ ] **Step 5: Update CI and documentation**

CI installs pnpm from the lockfile, runs `test`, `check`, `build`, and the build
verifier before Python packaging. README and INSTALL document Node.js only for
contributors, `yt-insights serve`, `--no-open`, loopback-only security, common
missing-index recovery, and the full research flow. ROADMAP moves local web UI
from conditional to implemented only after all gates pass.

- [ ] **Step 6: Run complete verification**

Run:

```bash
pnpm --dir web test
pnpm --dir web check
pnpm --dir web build
pnpm --dir web test:e2e
rtk .venv/bin/python scripts/verify_web_build.py
rtk .venv/bin/pytest -q
rtk .venv/bin/ruff check src tests scripts
rtk .venv/bin/mypy src
rtk .venv/bin/python -m build
rtk git diff --check
```

Expected: every command exits `0`; the existing suite still reports at least
`844 passed, 10 subtests passed` plus the new tests.

- [ ] **Step 7: Commit the integrated delivery**

```bash
git add web scripts/verify_web_build.py .github/workflows/ci.yml README.md INSTALL.md ROADMAP.md docs/IMPLEMENTATION-STATUS.md CHANGELOG.md tests/web/test_static_assets.py tests/test_packaging.py src/yt_insights/web/static
git commit -m "feat(web): ship the local Astro research interface"
```

## Frontend acceptance gate

- Dashboard, search, sources, research, and exports render in light and dark modes.
- Research always asks sufficiency before discovery and exact candidate approval before acquisition.
- Browser rendering uses safe DOM APIs and no external runtime assets.
- Keyboard navigation, focus movement, mobile layouts, and reduced motion work.
- Astro tests, strict checks, build, browser smoke, generated-asset verification, and wheel packaging pass.
- The packaged server runs without Node.js.
- Documentation states the local-only boundary and exact test commands.
