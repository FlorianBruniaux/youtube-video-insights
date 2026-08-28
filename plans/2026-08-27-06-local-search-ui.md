# Local Search UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal localhost-only interface for searching passages and navigating to timestamped YouTube moments after the CLI release gates pass.

**Architecture:** A standard-library HTTP adapter exposes the existing `SearchService`; static HTML/CSS/JavaScript renders results. The server binds only to loopback, returns a fixed JSON contract, and never executes search text as HTML.

**Tech Stack:** Python 3.11+ `http.server`, HTML, CSS, browser JavaScript, existing search service, pytest

**Spec:** `plans/specs/SEARCH-ARCHITECTURE.md`

## Global Constraints

- Do not start this plan until Plans 02 and 03 pass their release gates.
- The CLI and search service remain the source of behavior; the UI contains no independent ranking logic.
- Bind to `127.0.0.1` by default and reject non-loopback binding unless an explicit future security plan approves it.
- Use `textContent` and DOM construction for untrusted content; never assign search output to `innerHTML`.
- Do not ship API keys, transcript files, or absolute paths to the browser.
- Add no frontend build chain and no JavaScript package manager.

---

### Task 1: Define the local JSON API adapter

**Files:**
- Create: `src/yt_insights/web/__init__.py`
- Create: `src/yt_insights/web/api.py`
- Test: `tests/web/test_api.py`

**Interfaces:**
- Consumes: URL query parameters and `SearchService`
- Produces: `handle_search(params: Mapping[str, list[str]], service: SearchService) -> ApiResponse`

- [ ] **Step 1: Write failing API contract tests**

Assert success returns:

```json
{
  "query": "claude code",
  "count": 1,
  "results": [
    {
      "rank": 1,
      "title": "Build reliable agents",
      "channel": "example",
      "language": "fr",
      "start_seconds": 10.0,
      "snippet": "...",
      "youtube_url": "https://youtube.com/watch?v=nfupYzLjFGc&t=10s",
      "source_path": "example/transcripts/example.fr.vtt"
    }
  ]
}
```

Test missing query, repeated scalar parameters, invalid dates, limit above 100, and unknown parameters.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/web/test_api.py -q
```

Expected: web package is absent.

- [ ] **Step 3: Implement strict parameter conversion**

Reuse `SearchQuery` and rendering serialization. Return status `400` for invalid user input, `503` for missing/corrupt index, and `500` with a generic body for unexpected exceptions. Never return exception repr, SQL, or absolute paths.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/web/test_api.py -q
rtk git add src/yt_insights/web tests/web/test_api.py
rtk git commit -m "feat(web): expose local search API contract"
```

### Task 2: Implement the loopback HTTP server

**Files:**
- Create: `src/yt_insights/web/server.py`
- Test: `tests/web/test_server.py`

**Interfaces:**
- Consumes: `serve(service, *, host="127.0.0.1", port=8765)`
- Produces: static files, `GET /api/search`, `GET /api/status`, and no mutation endpoints

- [ ] **Step 1: Write failing route and header tests**

Start the server on an ephemeral loopback port and assert:

- `/api/search` delegates to the API adapter;
- non-GET methods return `405`;
- unknown paths return `404`;
- responses include `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`;
- no wildcard CORS header exists.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/web/test_server.py -q
```

Expected: server module is absent.

- [ ] **Step 3: Implement a bounded threaded server**

Use `ThreadingHTTPServer` with daemon request threads, a maximum URL length of 2,048 bytes, a per-request monotonic latency measurement, and fixed UTF-8 JSON/content types. Reject a configured host other than `127.0.0.1` or `::1`.

- [ ] **Step 4: Run and commit**

```bash
rtk .venv/bin/pytest tests/web/test_server.py -q
rtk git add src/yt_insights/web/server.py tests/web/test_server.py
rtk git commit -m "feat(web): serve search on loopback"
```

### Task 3: Build the static search interface

**Files:**
- Create: `src/yt_insights/web/static/index.html`
- Create: `src/yt_insights/web/static/app.js`
- Create: `src/yt_insights/web/static/styles.css`
- Test: `tests/web/test_static_assets.py`

**Interfaces:**
- Consumes: `/api/search` and `/api/status`
- Produces: keyboard-accessible filters, result list, and YouTube deep-link navigation

- [ ] **Step 1: Write static security and accessibility tests**

Assert assets contain one labelled search input, a submit button, status region with `aria-live`, filter controls, and result template. Assert JavaScript contains no `.innerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, or external script URL.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/web/test_static_assets.py -q
```

Expected: static assets are absent.

- [ ] **Step 3: Implement the interaction**

Submit with Enter or button, cancel the prior fetch using `AbortController`, serialize filters with `URLSearchParams`, and render every string with `document.createElement()` plus `textContent`. A result link opens the validated API-provided YouTube URL in a new tab with `rel="noopener noreferrer"`.

- [ ] **Step 4: Add empty, loading, and error states**

Display distinct messages for zero results, missing index, invalid query, aborted request, and server failure. Keep keyboard focus on the result heading after a successful search.

- [ ] **Step 5: Run and commit**

```bash
rtk .venv/bin/pytest tests/web/test_static_assets.py -q
rtk git add src/yt_insights/web/static tests/web/test_static_assets.py
rtk git commit -m "feat(web): add safe local search interface"
```

### Task 4: Package and expose `yt-insights serve`

**Files:**
- Modify: `pyproject.toml`
- Create: `src/yt_insights/cli_web.py`
- Modify: `src/yt_insights/cli.py`
- Test: `tests/web/test_cli_web.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: configured index path and optional loopback port
- Produces: `yt-insights serve --port 8765 --no-open`

- [ ] **Step 1: Write failing CLI tests**

Verify missing index exits `1`, invalid port exits `2`, server construction receives loopback host only, and `--no-open` performs no browser action.

- [ ] **Step 2: Verify failure**

```bash
rtk .venv/bin/pytest tests/web/test_cli_web.py -q
```

Expected: `serve` command is absent.

- [ ] **Step 3: Include static package data**

Configure setuptools package data for `web/static/*.html`, `*.js`, and `*.css`. Add a wheel-content test that builds the package and verifies all three assets are present.

- [ ] **Step 4: Implement CLI lifecycle**

Print the loopback URL, open the browser only when explicitly enabled and supported, serve until Ctrl-C, close the server in `finally`, and exit `0` after an intentional interrupt.

- [ ] **Step 5: Document the local security boundary**

State that `serve` is local-only, has no authentication, must not be reverse-proxied, and exposes indexed transcript snippets to the local browser.

- [ ] **Step 6: Run complete verification**

```bash
rtk .venv/bin/pytest tests/web -q
rtk .venv/bin/pytest -q
rtk .venv/bin/python -m build
rtk git diff --check
```

Expected: tests pass, build exits `0`, and the wheel contains static assets.

- [ ] **Step 7: Commit the packaged UI**

```bash
rtk git add pyproject.toml src/yt_insights/cli.py src/yt_insights/cli_web.py tests/web/test_cli_web.py README.md
rtk git commit -m "feat(web): expose packaged local search UI"
```

## Acceptance gate

- The local UI reuses the exact CLI search service and ranking.
- Server binds only to loopback.
- Static rendering contains no unsafe HTML insertion.
- No API key, transcript body, query log, or absolute path leaks to assets or errors.
- Keyboard-only search and result navigation work.
- Package build contains all static assets.
- Full suite and package build pass.

