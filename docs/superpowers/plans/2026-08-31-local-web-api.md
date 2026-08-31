# Local Web API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the existing search, catalog, research, acquisition, and export services through a bounded loopback-only JSON API.

**Architecture:** A dependency-injected application adapter maps strict HTTP requests to existing domain services. A standard-library server handles transport and security, while one bounded executor serializes discovery and acquisition jobs without becoming a second source of durable state.

**Tech Stack:** Python 3.11+, `http.server`, dataclasses, `concurrent.futures`, SQLite, Click, pytest

**Spec:** `docs/superpowers/specs/2026-08-31-local-astro-web-ui-design.md`

## Global Constraints

- Bind only to `127.0.0.1` or `::1`; the host is not configurable from the CLI.
- JSON responses use `schema_version: 1` and never expose absolute paths, cookies, secrets, SQL, raw exceptions, or transcript bodies.
- Request targets are at most 2,048 bytes and JSON bodies are at most 65,536 bytes.
- Mutations require `X-YT-Insights-Token` and the exact active process token.
- State-changing jobs use exactly one worker, at most 32 queued jobs, and at most 100 retained job records.
- Search and research behavior must delegate to the existing services.
- All user-facing error codes are fixed strings and never include untrusted input.
- No FastAPI, Pydantic, Redis, PostgreSQL, or remote bind.

---

## File map

| File | Responsibility |
|---|---|
| `src/yt_insights/web/models.py` | Immutable HTTP and job public records |
| `src/yt_insights/web/readers.py` | Bounded read-only catalog and export projections |
| `src/yt_insights/web/api.py` | Strict query and JSON parsing plus public serialization |
| `src/yt_insights/web/application.py` | Versioned route dispatch and domain-service orchestration |
| `src/yt_insights/web/jobs.py` | Single-writer executor and bounded public job registry |
| `src/yt_insights/web/security.py` | Loopback, host, token, and response-header policy |
| `src/yt_insights/web/server.py` | `http.server` transport and packaged static serving |
| `src/yt_insights/cli_web.py` | `yt-insights serve` lifecycle |

### Task 1: Add bounded research, catalog, and export read models

**Files:**
- Modify: `src/yt_insights/research/store.py`
- Create: `src/yt_insights/web/__init__.py`
- Create: `src/yt_insights/web/readers.py`
- Test: `tests/research/test_store.py`
- Test: `tests/web/test_readers.py`

**Interfaces:**
- Consumes: `ResearchStore`, `Catalog.open_read_only()`, and `DataPaths.exports`
- Produces: `ResearchStore.list_sessions(*, limit: int, offset: int) -> tuple[ResearchSession, ...]`
- Produces: `CatalogWebReader.list_sources(*, limit: int, offset: int) -> dict[str, object]`
- Produces: `ExportReader.list_exports(*, limit: int) -> dict[str, object]`

- [ ] **Step 1: Write the failing deterministic session-list test**

```python
def test_list_sessions_is_bounded_and_stably_sorted(store):
    first = create_session(store, session_id="a" * 32, topic="Older")
    second = create_session(store, session_id="b" * 32, topic="Newer")
    sessions = store.list_sessions(limit=1, offset=0)
    assert sessions == (second,)
    assert store.list_sessions(limit=1, offset=1) == (first,)
```

- [ ] **Step 2: Run the focused test and verify the missing method failure**

Run: `rtk .venv/bin/pytest tests/research/test_store.py -q`

Expected: FAIL because `ResearchStore.list_sessions` is absent.

- [ ] **Step 3: Implement the read-only query through the store connection guard**

```python
def list_sessions(self, *, limit: int, offset: int) -> tuple[ResearchSession, ...]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be non-negative")
    with self._connection() as connection:
        rows = connection.execute(
            "SELECT session_id FROM research_sessions "
            "ORDER BY updated_at DESC, session_id ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return tuple(self._session(connection, str(row[0])) for row in rows)
```

- [ ] **Step 4: Write reader tests using a real temporary catalog and exports directory**

Assert the catalog response contains only `video_id`, `title`, `published_at`,
`languages`, `sources`, `url`, and `artifact_count`. Assert export entries contain
only `name`, `session_id`, `created_at`, and `manifest_valid`. Create a symlinked
export, malformed manifest, and absolute path in fixture data and assert none is
followed or returned.

- [ ] **Step 5: Implement bounded read-only projections**

Use immutable SQLite reads through `Catalog.open_read_only()`. Reuse catalog
schema validation. Construct YouTube URLs only from validated 11-character
video IDs. Open export files relative to a directory file descriptor with
`O_NOFOLLOW`, cap manifests at 64 KiB, and return no filesystem path.

- [ ] **Step 6: Run focused tests**

Run: `rtk .venv/bin/pytest tests/research/test_store.py tests/web/test_readers.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the read models**

```bash
git add src/yt_insights/research/store.py src/yt_insights/web/__init__.py src/yt_insights/web/readers.py tests/research/test_store.py tests/web/test_readers.py
git commit -m "feat(web): add bounded local read models"
```

### Task 2: Define strict public contracts and route orchestration

**Files:**
- Create: `src/yt_insights/web/models.py`
- Create: `src/yt_insights/web/api.py`
- Create: `src/yt_insights/web/application.py`
- Test: `tests/web/test_api.py`
- Test: `tests/web/test_application.py`

**Interfaces:**
- Consumes: `SearchService`, `CatalogWebReader`, `ResearchWorkflow`, `ResearchStore`, `ExportReader`, and `JobExecutor`
- Produces: `WebRequest(method, path, query, headers, body)` and `WebRequest.get(path, query)`
- Produces: `WebResponse.json(status: int, payload: Mapping[str, object]) -> WebResponse`
- Produces: `WebApplication.handle(request: WebRequest) -> WebResponse`

- [ ] **Step 1: Write failing contract tests**

```python
def test_search_delegates_to_existing_service(fake_services):
    app = build_test_application(fake_services)
    response = app.handle(WebRequest.get("/api/v1/search", "q=local&limit=10"))
    assert response.status == 200
    assert response.json_body["schema_version"] == 1
    assert response.json_body["hits"][0]["url"].startswith("https://youtube.com/watch?")
    assert fake_services.search_queries[0].text == "local"

def test_stale_revision_is_a_fixed_conflict(fake_services):
    fake_services.workflow_error = ResearchRevisionConflict("private state")
    response = fake_services.app.handle(decision_request(revision=4))
    assert response.status == 409
    assert response.json_body == {
        "schema_version": 1,
        "error": {"code": "stale_revision"},
    }
```

Cover repeated scalar query parameters, unknown fields, booleans used as
integers, invalid session IDs, more than 5 approved IDs, unknown routes, and
exception strings containing an absolute path.

- [ ] **Step 2: Run tests and verify imports fail**

Run: `rtk .venv/bin/pytest tests/web/test_api.py tests/web/test_application.py -q`

Expected: FAIL because the web contracts do not exist.

- [ ] **Step 3: Implement immutable transport records**

```python
@dataclass(frozen=True, slots=True)
class WebRequest:
    method: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    headers: Mapping[str, str]
    body: bytes = b""

@dataclass(frozen=True, slots=True)
class WebResponse:
    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()
```

- [ ] **Step 4: Implement closed-world parsers**

`parse_search`, `parse_pagination`, `parse_start_session`, `parse_decision`,
`parse_approval`, `parse_acquisition`, and `parse_export` accept only documented
keys. Strings are stripped, nonblank, NUL-free, and capped at 500 characters.
Idempotency keys are 1 to 200 printable ASCII characters. Approved IDs are a
deduplicated tuple of 1 to 5 validated YouTube IDs.

- [ ] **Step 5: Implement route dispatch without CLI subprocesses**

Map every spec route directly to a dependency method. Serialize search hits
with the same fields and clipping rules as the MCP facade. Serialize research
snapshots using `ResearchResponse.to_dict()`. Submit discovery, acquisition,
and retry to `JobExecutor`; return `202` with the opaque job ID.

For source preview, submit an injected acquisition facade that calls
`classify_source`, `fetch_video_list`, and `build_acquisition_plan`. Return a
job whose safe plan projection excludes every directory field. For confirmed source
acquisition, submit `execute_acquisition` to the same single-writer executor;
the browser must send the exact preview fingerprint so a changed plan returns
`409 plan_changed` instead of acquiring a different set.

- [ ] **Step 6: Add fixed exception mapping**

Map validation to `invalid_request`, missing resources to `not_found`, revision
conflicts to `stale_revision`, queue capacity to `job_queue_full`, unavailable
indexes or providers to their bounded `*_unavailable` code, and every other
exception to `internal_error`. Log exception types server-side without request
bodies or exception messages.

- [ ] **Step 7: Run focused tests**

Run: `rtk .venv/bin/pytest tests/web/test_api.py tests/web/test_application.py -q`

Expected: PASS.

- [ ] **Step 8: Commit the API application layer**

```bash
git add src/yt_insights/web/models.py src/yt_insights/web/api.py src/yt_insights/web/application.py tests/web/test_api.py tests/web/test_application.py
git commit -m "feat(web): expose versioned application API"
```

### Task 3: Implement the bounded single-writer job executor

**Files:**
- Create: `src/yt_insights/web/jobs.py`
- Test: `tests/web/test_jobs.py`

**Interfaces:**
- Consumes: zero-argument callables returning public JSON mappings
- Produces: `JobExecutor.submit(kind: str, operation: Callable[[], Mapping[str, object]]) -> JobSnapshot`
- Produces: `JobExecutor.get(job_id: str) -> JobSnapshot`
- Produces: `JobExecutor.close() -> None`

- [ ] **Step 1: Write failing lifecycle and capacity tests**

```python
def test_jobs_run_serially_and_publish_bounded_results(blocking_operation):
    jobs = JobExecutor(max_queued=2, max_records=3, id_factory=iter_ids())
    first = jobs.submit("discovery", blocking_operation("first"))
    second = jobs.submit("acquisition", blocking_operation("second"))
    assert jobs.get(first.job_id).status == "running"
    assert jobs.get(second.job_id).status == "queued"

def test_queue_full_does_not_start_or_evict_running_work(block_forever):
    jobs = JobExecutor(max_queued=1, max_records=3, id_factory=iter_ids())
    jobs.submit("discovery", block_forever)
    jobs.submit("acquisition", lambda: {})
    with pytest.raises(JobQueueFull):
        jobs.submit("source_preview", lambda: {})
```

Also assert no retry, result clipping to 24 KiB, fixed `operation_failed`, FIFO
eviction of terminal records only, unknown job behavior, and idempotent close.

- [ ] **Step 2: Run tests and verify the module is absent**

Run: `rtk .venv/bin/pytest tests/web/test_jobs.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement one-worker execution with an explicit semaphore**

Use `ThreadPoolExecutor(max_workers=1, thread_name_prefix="yt-insights-web")`.
Reserve queue capacity before submitting, transition snapshots under one lock,
release capacity in `finally`, and store only public result mappings. Generate
IDs with `secrets.token_urlsafe(24)` in production.

- [ ] **Step 4: Run the race-focused tests**

Run: `rtk .venv/bin/pytest tests/web/test_jobs.py -q`

Expected: PASS with no order-dependent failures.

- [ ] **Step 5: Commit the executor**

```bash
git add src/yt_insights/web/jobs.py tests/web/test_jobs.py
git commit -m "feat(web): serialize local mutation jobs"
```

### Task 4: Add the loopback security policy and HTTP server

**Files:**
- Create: `src/yt_insights/web/security.py`
- Create: `src/yt_insights/web/server.py`
- Test: `tests/web/test_security.py`
- Test: `tests/web/test_server.py`

**Interfaces:**
- Consumes: `WebApplication`, packaged static directory, process token
- Produces: `create_server(app: WebApplication, *, host: str, port: int, static_root: Traversable) -> ThreadingHTTPServer`
- Produces: `security_headers(*, api: bool) -> tuple[tuple[str, str], ...]`

- [ ] **Step 1: Write failing loopback and header tests**

Start on port `0` and assert valid GETs work on loopback. Assert non-loopback
host construction fails, invalid `Host` returns `403`, API responses have no
CORS header, mutations without the token return `403`, target length 2,049
returns `414`, body length 65,537 returns `413`, and unknown static paths return
`404` without directory listings.

- [ ] **Step 2: Write static confinement tests**

Request `/../pyproject.toml`, percent-encoded traversal, doubled separators,
symlinked assets, unknown extensions, and a route resembling an absolute path.
Assert none returns file contents. Assert `/research/<safe-id>` serves the
packaged research workspace shell only after validating the ID.

- [ ] **Step 3: Run tests and verify failure**

Run: `rtk .venv/bin/pytest tests/web/test_security.py tests/web/test_server.py -q`

Expected: FAIL.

- [ ] **Step 4: Implement security policy**

Use exact loopback host sets, exact active port matching, and bracketed IPv6
host parsing. Apply `default-src 'self'`, no object or frame ancestors, no
external connections, `nosniff`, `no-referrer`, restrictive permissions, and
`no-store` for API and bootstrap responses.

- [ ] **Step 5: Implement transport**

Parse the target with `urllib.parse.urlsplit`, reject malformed percent
encoding, cap input before parsing, read exact bounded bodies, and construct
`WebRequest`. Serve static files by a fixed route table and package resources,
not by joining untrusted filesystem paths. Inject the mutation token through a
small same-origin bootstrap response, not a cookie.

- [ ] **Step 6: Run focused tests**

Run: `rtk .venv/bin/pytest tests/web/test_security.py tests/web/test_server.py -q`

Expected: PASS.

- [ ] **Step 7: Commit the secure server**

```bash
git add src/yt_insights/web/security.py src/yt_insights/web/server.py tests/web/test_security.py tests/web/test_server.py
git commit -m "feat(web): serve the API on guarded loopback"
```

### Task 5: Expose the server through the CLI

**Files:**
- Create: `src/yt_insights/cli_web.py`
- Modify: `src/yt_insights/cli.py`
- Modify: `pyproject.toml`
- Test: `tests/web/test_cli_web.py`
- Modify: `tests/test_packaging.py`

**Interfaces:**
- Consumes: configured `DataPaths`
- Produces: `yt-insights serve --port 8765 --no-open`

- [ ] **Step 1: Write failing CLI tests**

Verify port `0`, port `65536`, and booleans are rejected; the host is fixed to
`127.0.0.1`; browser opening occurs once by default and never with `--no-open`;
Ctrl-C closes the HTTP server and executor; missing databases produce a bounded
Click error without an absolute path.

- [ ] **Step 2: Run the focused tests**

Run: `rtk .venv/bin/pytest tests/web/test_cli_web.py -q`

Expected: FAIL because `serve` is not registered.

- [ ] **Step 3: Implement the Click command and lifecycle**

```python
@click.command("serve")
@click.option("--port", type=click.IntRange(1, 65535), default=8765, show_default=True)
@click.option("--no-open", is_flag=True, help="Do not open the local browser.")
def serve_command(port: int, no_open: bool) -> None:
    """Serve the local YT Insights interface on loopback."""
```

Construct dependencies once, print the exact URL, open it only after binding,
serve until interruption, and close jobs plus server in `finally`.

- [ ] **Step 4: Include `web/static/**/*` as package data and test the wheel**

Build a wheel in the packaging test and assert it contains `index.html`, hashed
Astro assets, the research workspace shell, and no source maps or `node_modules`.

- [ ] **Step 5: Run backend verification**

Run:

```bash
rtk .venv/bin/pytest tests/web tests/research/test_store.py tests/test_packaging.py -q
rtk .venv/bin/ruff check src tests
rtk .venv/bin/mypy src
rtk git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 6: Commit the CLI integration**

```bash
git add src/yt_insights/cli_web.py src/yt_insights/cli.py pyproject.toml tests/web/test_cli_web.py tests/test_packaging.py
git commit -m "feat(web): expose the packaged local server"
```

## Backend acceptance gate

- Every documented API route has a focused contract test.
- Search delegates to `SearchService` and research delegates to `ResearchWorkflow`.
- Session, source, export, and job collections are bounded and deterministically ordered.
- Loopback, host, token, size, path-confinement, and security-header tests pass.
- Jobs serialize mutations and never retry automatically.
- No public fixture or error response contains an absolute path or exception text.
- The existing Python suite, Ruff, mypy, packaging test, and diff check pass.
