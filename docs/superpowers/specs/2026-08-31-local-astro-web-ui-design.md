# Local Astro Web UI Design

**Status:** approved for local implementation

**Date:** 2026-08-31

**Supersedes:** `plans/2026-08-27-06-local-search-ui.md`

## 1. Product goal

Add a local-first web interface to the existing YT Insights corpus and
cumulative research workflow. The interface makes the current Python services
usable without memorizing CLI commands while preserving every approval,
revision, provenance, and bounded-error guarantee already enforced by the
domain layer.

The V1 is a local application, not a hosted service. It serves a prebuilt Astro
application from the installed Python package and exposes a loopback-only JSON
API backed by the existing SQLite databases and Python services.

## 2. User outcomes

The interface must let a user:

1. inspect the health and size of the local corpus;
2. search timestamped transcript passages;
3. inspect indexed sources and open their YouTube pages;
4. create and resume cumulative research sessions;
5. answer the explicit sufficiency question;
6. request discovery only after choosing to refresh;
7. approve exact candidate video IDs before acquisition;
8. follow queued discovery and acquisition jobs;
9. inspect evidence, decisions, candidates, acquisition history, and events;
10. find and open exported research dossiers.

The UI must not make a research decision, acquire a video, replace a stale
session state, or retry an operation without an explicit user action.

## 3. Explicit non-goals

- No hosted or remotely accessible mode.
- No user accounts, authentication, teams, sharing, or administration.
- No PocketBase, PostgreSQL, Redis, Qdrant, or graph database.
- No vector search or independent browser-side ranking.
- No transcript audio generation or audio transcription.
- No writable MCP tools.
- No browser extension.
- No automatic discovery, acquisition, retry, or session replacement.
- No React, Preact, Vue, or other client framework in V1.
- No Node.js runtime after the package has been built.

## 4. Architecture

```text
Browser
  -> static Astro HTML, CSS, and TypeScript-generated JavaScript
  -> JSON /api/v1
Loopback Python server
  -> HTTP validation and public response adapters
  -> bounded single-writer job executor
Existing application services
  -> SearchService
  -> read-only catalog projection
  -> ResearchWorkflow
  -> acquisition and export services
Durable local data
  -> VTT and metadata files
  -> catalog.sqlite3
  -> .search/search-v1.sqlite3
  -> .research/research-v1.sqlite3
```

Astro generates static assets. The Python process serves these assets and the
API. Browser code contains presentation and request orchestration only. Domain
validation, state transitions, ranking, provenance, acquisition, and exports
remain in Python.

## 5. Project structure

```text
web/
  package.json
  pnpm-lock.yaml
  astro.config.mjs
  tsconfig.json
  src/
    components/
    layouts/
    lib/
    pages/
    styles/
  tests/

src/yt_insights/web/
  __init__.py
  api.py
  application.py
  errors.py
  jobs.py
  security.py
  server.py
  static/

tests/web/
  test_api.py
  test_application.py
  test_jobs.py
  test_security.py
  test_server.py
  test_static_assets.py
  test_cli_web.py
```

Focused files may be split further when a responsibility would otherwise mix
HTTP transport, domain adaptation, or presentation.

## 6. Screen architecture

### `/`

Progressive dashboard with:

- corpus health and counts;
- global transcript search;
- add a YouTube URL action;
- recent research sessions;
- resumable session warnings;
- recent exports.

### `/search`

Global passage search with query, channel, language, result limit, timestamped
excerpts, and YouTube deep links. The API remains the only ranking authority.

### `/sources`

Paginated corpus inventory with title, channel, language, upload date,
transcript state, index state, and validated YouTube link.

### `/research/new`

Form for topic, one or more search queries, languages, and freshness profile.
Submitting creates a durable research session and redirects to its workspace.

### `/research/:id`

Evidence workspace with:

- evidence and coverage in the main column;
- the required decision in a secondary column;
- freshness and date coverage;
- candidates after discovery;
- acquisition attempts and bounded failures;
- a collapsible event timeline;
- export action after an eligible state.

On small screens the decision panel follows the evidence panel.

### `/exports`

Bounded list of deterministic exports with session, creation date, manifest
status, and a local open action exposed by a safe API projection.

### Navigation

```text
YT Insights | Research | Corpus | Sources | Exports | Theme
```

Light mode is the default. The selected light or dark preference is stored in
browser-local storage. The UI respects reduced-motion and keyboard navigation.

## 7. API contract

All JSON responses include `schema_version: 1`. Public payloads contain no
absolute path, cookie selector, API key, SQL, raw exception, transcript file,
or unbounded diagnostic data.

### Reads

- `GET /api/v1/status`
- `GET /api/v1/search`
- `GET /api/v1/sources`
- `GET /api/v1/research/sessions`
- `GET /api/v1/research/sessions/{session_id}`
- `GET /api/v1/exports`
- `GET /api/v1/jobs/{job_id}`

### Mutations

- `POST /api/v1/sources/preview`
- `POST /api/v1/sources/acquire`
- `POST /api/v1/research/sessions`
- `POST /api/v1/research/sessions/{session_id}/decisions`
- `POST /api/v1/research/sessions/{session_id}/discovery`
- `POST /api/v1/research/sessions/{session_id}/approvals`
- `POST /api/v1/research/sessions/{session_id}/acquisition`
- `POST /api/v1/research/sessions/{session_id}/retry`
- `POST /api/v1/research/sessions/{session_id}/exports`

Mutation bodies carry the expected session revision when the domain operation
requires one. Non-idempotent mutations also carry a client-generated
idempotency key. The adapter validates the public request before invoking the
workflow.

Source preview, research discovery, and acquisition return `202` with a
`job_id`. The browser polls the job resource with bounded backoff. Other
successful mutations return their public snapshot directly.

### Error envelope

```json
{
  "schema_version": 1,
  "error": {
    "code": "stale_revision"
  }
}
```

Supported transport mappings:

| Status | Public meaning |
|---:|---|
| 400 | invalid request |
| 403 | invalid mutation token or host |
| 404 | unknown resource |
| 409 | stale revision or incompatible workflow state |
| 413 | URL or JSON body too large |
| 429 | writer queue full |
| 503 | required local index or provider unavailable |
| 500 | generic internal failure |

Untrusted input and exception text must never be copied into the error body.

## 8. Application adapters

The web application factory receives explicit dependencies rather than reading
process globals. It constructs:

- the existing search service from the configured search database;
- a read-only catalog projection from `catalog.sqlite3`;
- the existing research workflow from the configured data root;
- a bounded export reader;
- the writer executor.

The UI needs one new read-only store projection:

```python
ResearchStore.list_sessions(*, limit: int, offset: int) -> tuple[ResearchSession, ...]
```

Results are sorted by `updated_at` descending with `session_id` as the stable
tie breaker. The public list is bounded to 100 items per request.

Any catalog projection added for the UI must be read-only, paginated, and
limited to fields already safe for the CLI or MCP. It must not expose source
filesystem paths.

## 9. Job execution and concurrency

The server uses one process-local executor with:

- exactly one worker for state-changing work;
- at most 32 queued jobs;
- at most 100 retained public job records;
- states `queued`, `running`, `succeeded`, and `failed`;
- random opaque job identifiers;
- bounded public result and error codes;
- no automatic restart after process termination.

Durable workflow state remains in SQLite. The job registry is intentionally
ephemeral. After a restart, the UI reloads the durable session and offers only
the actions allowed by its current state.

No acquisition or discovery is retried automatically. A stale revision
returns `409`; the browser reloads the current snapshot and asks the user to
decide again.

## 10. Local security boundary

The server:

- binds only to `127.0.0.1` or `::1`;
- validates `Host` against the active loopback address and port;
- emits no CORS headers;
- injects a random per-process mutation token into the initial bootstrap;
- requires that token in `X-YT-Insights-Token` for every mutation;
- accepts JSON bodies of at most 64 KiB;
- accepts request targets of at most 2,048 bytes;
- emits `Content-Security-Policy`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, and a restrictive permissions policy;
- emits `Cache-Control: no-store` for API and bootstrap responses;
- never returns directory listings;
- never serves files outside the packaged static directory.

The browser:

- creates untrusted content with DOM nodes and `textContent`;
- never uses `innerHTML`, `insertAdjacentHTML`, `document.write`, or `eval`;
- opens only API-provided `https://www.youtube.com/watch` URLs;
- uses `rel="noopener noreferrer"` for new tabs;
- sends the mutation token only to same-origin API routes.

There is no authentication because the application is loopback-only. The
documentation must explicitly forbid exposing it through a reverse proxy or a
public bind.

## 11. Frontend implementation

Use Astro 7 with strict TypeScript and no component framework. Pages are
pre-rendered and share:

- semantic HTML landmarks;
- a skip link;
- visible `:focus-visible` states;
- light and dark design tokens;
- reduced-motion fallbacks;
- reusable cards, badges, tables, empty states, and status notices;
- mobile-first layouts;
- progressive enhancement for API data.

The visual language may reuse the clarity of `more-than-fan` for video cards,
processing states, and timestamp actions. It may reuse the portfolio and guide
landing conventions for tokens, themes, accessibility, and responsive layout.
It must not copy their data stack or introduce a second backend.

All user-facing prompts and interface copy are written in English.

## 12. Build and packaging

`web/` is the editable Astro source. `pnpm build` generates static assets into
`src/yt_insights/web/static/`. Generated assets are committed so an installed
Python wheel can run without Node.js.

The Python package includes the generated assets as package data. CI rebuilds
the Astro application and fails if the generated output differs from the
committed assets.

The CLI exposes:

```text
yt-insights serve --port 8765 --no-open
```

The host is not configurable. The browser opens by default for interactive use
and `--no-open` disables that side effect for tests and automation. The server
prints the exact loopback URL and stops cleanly on Ctrl-C.

## 13. Verification strategy

### Python

- Unit tests for request parsing, response projection, and error mapping.
- Store tests for bounded, deterministic session listing.
- Job executor tests for capacity, states, eviction, and no automatic retry.
- HTTP tests against an ephemeral loopback port.
- Security-header, host-validation, token, URL-size, and body-size tests.
- CLI tests for port validation, browser opening, and graceful shutdown.
- Wheel-content test for every packaged static asset.

### Frontend

- `astro check` with strict TypeScript.
- Unit tests for pure request and view-state helpers.
- Static scans that reject unsafe DOM APIs and external scripts.
- One browser smoke test covering dashboard load, search, theme persistence,
  and the required-decision state with mocked API fixtures.
- Keyboard and reduced-motion checks in the smoke fixture.

### Whole project

- Existing Python suite remains green.
- Ruff and mypy remain green.
- Astro build is reproducible from the lockfile.
- Python build produces a wheel containing the static application.
- `git diff --check` passes.
- A manual local smoke run exercises the packaged `yt-insights serve` command.

## 14. Delivery slices

1. Public read models and session listing.
2. Loopback API, security boundary, and job executor.
3. Astro shell, theme, dashboard, search, and sources.
4. Research workspace, decisions, candidates, jobs, and exports.
5. Packaging, CLI, browser smoke test, docs, and release evidence.

Each slice must pass its focused tests before the next slice depends on it.

## 15. Acceptance criteria

- The dashboard, search, sources, research, and exports routes render locally.
- Search results match the existing `SearchService` behavior.
- Research mutations use the existing `ResearchWorkflow` and preserve revision
  and idempotency rules.
- The user is asked whether evidence is sufficient before any discovery.
- Candidate acquisition requires a separate exact-ID approval.
- Discovery and acquisition expose bounded jobs without automatic retry.
- No absolute path, transcript body, cookie, secret, SQL, or traceback reaches
  the browser.
- The server rejects non-loopback binding, invalid hosts, oversized requests,
  and mutation requests without the process token.
- The browser contains no unsafe HTML insertion or external runtime script.
- The wheel runs the interface without Node.js installed.
- Existing CLI, MCP, agent assets, search, and research tests remain green.
