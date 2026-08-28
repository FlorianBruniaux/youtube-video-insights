# Agent-Ready Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `yt-insights` callable from any directory through stable, machine-readable commands for diagnosis, acquisition, export and read-only MCP research.

**Architecture:** The Python package owns every executable rule. A resolved `data_root` removes dependence on the current directory, an acquisition service replaces shell runbooks for agent use, and the MCP stays a thin read-only adapter over the catalog and transcript index.

**Tech Stack:** Python 3.11+, Click, yt-dlp subprocess, SQLite/FTS5, MCP Python SDK, pytest, uv

**Spec:** `plans/specs/AGENT-PLATFORM.md`

## Global Constraints

- Preserve the current `run`, `list`, `report`, `index`, `search`, `catalog`, `suggest-shorts` and `generate-short` commands.
- Preserve the modified `.claude/skills/yt-add-channel.md`, `CLAUDE.md` and `runbook/run-channel.sh` until the integration migration handles them explicitly.
- Default storage remains `output/` only when neither CLI, environment nor user TOML configures a global data root.
- Never echo API keys, bearer tokens, cookie values or URL credentials.
- Acquisition of a channel, playlist or batch must not start without `--yes`.
- Browser cookies require an explicit `--cookies-from-browser` value.
- The MCP remains read-only and exposes exactly four tools after this plan.
- Do not add audio transcription. Missing YouTube subtitles remain a reported collection error.
- Every file write uses a temporary sibling and `os.replace()`.
- Run the full suite with the `mcp` and `dev` extras before each merge.
- Task 1 lands before Tasks 2, 4 and 5. Those three tasks own isolated command modules and never edit `cli.py`; one coordinator registers them after their contracts pass.
- Optional Task D1 does not block the agent-facing runtime or global Claude Code and Codex integration.

---

### Task 1: Resolve one data root outside the current directory

**Files:**
- Create: `src/yt_insights/paths.py`
- Modify: `src/yt_insights/config.py`
- Modify: `src/yt_insights/cli_search.py`
- Modify: `src/yt_insights/mcp_server.py`
- Test: `tests/test_paths.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Consumes: CLI override, `YT_INSIGHTS_DATA_ROOT`, TOML `data_root`, legacy path overrides
- Produces: `DataPaths.from_root(root: Path) -> DataPaths` and `Config.data_paths -> DataPaths`

- [ ] **Step 1: Add failing precedence and derivation tests**

```python
def test_data_paths_derive_every_database_and_artifact_directory(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")

    assert paths.transcripts == tmp_path / "corpus" / "transcripts"
    assert paths.insights == tmp_path / "corpus" / "insights"
    assert paths.exports == tmp_path / "corpus" / "exports"
    assert paths.catalog_database == tmp_path / "corpus" / "catalog.sqlite3"
    assert paths.search_database == tmp_path / "corpus" / ".search" / "search-v1.sqlite3"
```

Add cases proving this precedence:

```text
explicit override > environment > TOML > output/
```

Keep a regression test proving `YT_INSIGHTS_TRANSCRIPTS_DIR` still overrides only the transcript directory.

- [ ] **Step 2: Run the focused tests and record the expected failure**

```bash
rtk pytest tests/test_paths.py tests/test_config.py -q
```

Expected: import failure for `yt_insights.paths`.

- [ ] **Step 3: Implement the immutable path contract**

```python
@dataclass(frozen=True)
class DataPaths:
    root: Path
    transcripts: Path
    insights: Path
    shorts: Path
    clips: Path
    exports: Path
    catalog_database: Path
    search_database: Path

    @classmethod
    def from_root(cls, root: Path) -> "DataPaths":
        resolved = root.expanduser().resolve(strict=False)
        return cls(
            root=resolved,
            transcripts=resolved / "transcripts",
            insights=resolved / "insights",
            shorts=resolved / "shorts",
            clips=resolved / "clips",
            exports=resolved / "exports",
            catalog_database=resolved / "catalog.sqlite3",
            search_database=resolved / ".search" / "search-v1.sqlite3",
        )
```

Add `data_root` and `exports_dir` to `Config`. Derive default artifact paths after the four configuration layers merge, then apply legacy path overrides only to their named field.

- [ ] **Step 4: Replace adapter defaults with `Config.data_paths`**

`cli_search.py` and `mcp_server.py` must no longer derive an operational database from `Path("output")` after configuration has loaded. Keep the constants only as documented compatibility defaults.

- [ ] **Step 5: Verify paths from an unrelated current directory**

```bash
rtk pytest tests/test_paths.py tests/test_config.py tests/search/test_cli_search.py tests/test_mcp_server.py -q
rtk git diff --check
```

Add an integration test that creates a user TOML with one absolute `data_root`,
runs search from two distinct current directories, and proves both runs resolve
the same catalog and search database without an environment override.

- [ ] **Step 6: Commit the path contract**

```bash
rtk git add src/yt_insights/paths.py src/yt_insights/config.py src/yt_insights/cli_search.py src/yt_insights/mcp_server.py tests/test_paths.py tests/test_config.py tests/search/test_cli_search.py tests/test_mcp_server.py
rtk git commit -m "feat: resolve a global corpus data root"
```

### Task 2: Add a secret-safe diagnostic command for agents

**Files:**
- Create: `src/yt_insights/doctor.py`
- Create: `src/yt_insights/cli_doctor.py`
- Test: `tests/test_doctor.py`
- Test: `tests/test_cli_doctor.py`

**Interfaces:**
- Consumes: `Config`, resolved paths, executable discovery and optional local backend probes
- Produces: `DoctorReport` and `yt-insights doctor [--json] [--probe-backends]`

- [ ] **Step 1: Write failing report tests**

```python
def test_doctor_json_contains_no_secret_values(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-appear")
    report = inspect_runtime(Config(data_root=tmp_path, api_key="also-secret"))
    payload = json.dumps(report.to_dict())

    assert "must-not-appear" not in payload
    assert "also-secret" not in payload
    assert report.data_root == str(tmp_path.resolve())
```

Cover missing `yt-dlp`, missing `ffmpeg`, absent index, valid index, configured cloud credential presence and unreachable local backend. Credential status is a boolean, never a value.

- [ ] **Step 2: Verify the command is absent**

```bash
rtk pytest tests/test_doctor.py tests/test_cli_doctor.py -q
```

Expected: import or Click command failure.

- [ ] **Step 3: Implement stable status objects**

```python
@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Literal["pass", "warn", "fail", "unknown"]
    detail: str

@dataclass(frozen=True)
class DoctorReport:
    data_root: str
    checks: tuple[CheckResult, ...]
```

Use `shutil.which()` for executables and never expose resolved executable paths. Validate an existing FTS database with `SQLiteFtsIndex.status()`, but do not instantiate `Catalog`: its constructor initializes SQLite and would make `doctor` mutating. A missing `yt-dlp` or an existing invalid search index is a failure. Missing `ffmpeg`, an absent search index, an absent catalog database and absent cloud credentials are warnings or unknown states.

`--probe-backends` is strictly bounded to `GET` requests against the hard-coded localhost cc-bridge and Ollama health endpoints. It must not call `resolve_backend()`, submit a completion request or contact a paid remote API.

- [ ] **Step 4: Implement an unregistered Click command with deterministic JSON and readable text output**

```json
{
  "data_root": "/absolute/corpus",
  "checks": [
    {"name": "yt-dlp", "status": "pass", "detail": "available"},
    {"name": "search-index", "status": "pass", "detail": "3270 documents"}
  ]
}
```

Exit `0` for `pass` or `warn`, `1` when a required local component fails, and `2` for invalid CLI input.

- [ ] **Step 5: Run focused and full tests**

```bash
rtk pytest tests/test_doctor.py tests/test_cli_doctor.py -q
rtk pytest -q
```

- [ ] **Step 6: Commit diagnostics without editing the root CLI**

```bash
rtk git add src/yt_insights/doctor.py src/yt_insights/cli_doctor.py tests/test_doctor.py tests/test_cli_doctor.py
rtk git commit -m "feat: expose agent-safe runtime diagnostics"
```

### Optional Task D1: Make backend choice explicit and repair MLX direct mode

This optimization starts after the required runtime and agent integration. It
must not delay search, acquisition, export, MCP or global installation because
none of those paths needs an LLM. Do not run it in parallel with Task 1 because
both tasks own `config.py` and `tests/test_config.py`.

**Files:**
- Modify: `src/yt_insights/config.py`
- Modify: `src/yt_insights/backends/__init__.py`
- Modify: `src/yt_insights/backends/mlx.py`
- Modify: `src/yt_insights/cli.py`
- Modify: `src/yt_insights/wizard.py`
- Modify: `pyproject.toml`
- Test: `tests/test_backends.py`
- Test: `tests/test_config.py`
- Test: `tests/test_wizard.py`

**Interfaces:**
- Consumes: `backend=auto|ollama|mlx|cc-bridge|anthropic|openai`, model name and endpoint
- Produces: deterministic `ResolvedBackend` identity and one loaded MLX model per backend instance

- [ ] **Step 1: Write failing provider selection tests**

```python
def test_explicit_mlx_never_probes_http(monkeypatch) -> None:
    monkeypatch.setattr(backends.httpx, "Client", lambda *args, **kwargs: pytest.fail("HTTP probe"))
    monkeypatch.setattr(backends, "MLXBackend", FakeMLXBackend)

    resolved = resolve_backend(Config(backend="mlx", model="mlx-community/Qwen3-4B"))

    assert resolved.identity.backend == "mlx"
    assert resolved.identity.endpoint == "local://mlx"
```

Add tests for an invalid backend name, explicit Ollama without installed model, explicit Anthropic without a key, and unchanged `auto` order.

- [ ] **Step 2: Write a failing MLX load-once test**

```python
def test_mlx_loads_model_once_and_reuses_it(fake_mlx_lm) -> None:
    backend = MLXBackend(Config(backend="mlx", model="test-model"))
    backend.generate("one", max_tokens=8, timeout=10)
    backend.generate("two", max_tokens=8, timeout=10)

    assert fake_mlx_lm.load_calls == ["test-model"]
    assert len(fake_mlx_lm.generate_calls) == 2
```

- [ ] **Step 3: Verify failures**

```bash
rtk pytest tests/test_backends.py tests/test_config.py tests/test_wizard.py -q
```

- [ ] **Step 4: Implement explicit backend routing**

Add `backend: str = "auto"` to `Config` and `YT_INSIGHTS_BACKEND` to the environment layer. Preserve the current automatic order only when `backend == "auto"`.

Repair MLX with the public `mlx_lm` contract:

```python
self._model, self._tokenizer = mlx_lm.load(config.model)
text = mlx_lm.generate(
    self._model,
    self._tokenizer,
    prompt=prompt,
    max_tokens=max_tokens,
    verbose=False,
)
```

Reject an empty or default cloud model when MLX is explicit. Keep effective concurrency at `1`.

- [ ] **Step 5: Add CLI and wizard selection**

Expose `--backend` on LLM commands. The wizard lists only configured or detected routes and prints the resolved public identity before the first model call.

- [ ] **Step 6: Validate without loading a real large model**

```bash
rtk pytest tests/test_backends.py tests/test_config.py tests/test_wizard.py -q
rtk pytest -q
```

The automated gate uses a fake `mlx_lm` module. A real one-token MLX canary is a manual acceptance test because it may allocate several gigabytes.

- [ ] **Step 7: Commit backend selection**

```bash
rtk git add src/yt_insights/config.py src/yt_insights/backends/__init__.py src/yt_insights/backends/mlx.py src/yt_insights/cli.py src/yt_insights/wizard.py pyproject.toml tests/test_backends.py tests/test_config.py tests/test_wizard.py
rtk git commit -m "feat: select local and cloud backends explicitly"
```

### Task 4: Introduce dry-run-first source acquisition

**Files:**
- Create: `src/yt_insights/acquisition.py`
- Create: `src/yt_insights/cli_acquire.py`
- Modify: `src/yt_insights/downloader.py`
- Modify: `src/yt_insights/catalog.py`
- Test: `tests/test_acquisition.py`
- Test: `tests/test_cli_acquire.py`
- Modify: `tests/test_downloader.py`
- Modify: `tests/test_catalog.py`

**Interfaces:**
- Consumes: URL or batch path, optional channel slug, years, language and analysis flag
- Produces: `build_acquisition_plan(...) -> AcquisitionPlan` and `execute_acquisition(plan, ...) -> AcquisitionReport`

- [ ] **Step 1: Define failing source classification tests**

```python
@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("https://www.youtube.com/watch?v=nfupYzLjFGc", "video"),
        ("https://youtu.be/nfupYzLjFGc", "video"),
        ("https://www.youtube.com/playlist?list=PL123", "playlist"),
        ("https://www.youtube.com/@example/videos", "channel"),
    ],
)
def test_classify_source(source: str, kind: str) -> None:
    assert classify_source(source).value == kind
```

Add `batch` for an existing regular file and reject unsupported hosts, missing files, NUL bytes and ambiguous YouTube URLs.

- [ ] **Step 2: Define failing plan tests**

```python
def test_channel_plan_requires_confirmation(tmp_path: Path) -> None:
    plan = build_acquisition_plan(
        source="https://www.youtube.com/@example/videos",
        data_paths=DataPaths.from_root(tmp_path),
        slug="example",
        years={2025, 2026},
        discovered=sample_videos(42),
    )

    assert plan.requires_confirmation is True
    assert plan.selected_count == 42
    assert plan.output_root == tmp_path / "example"
```

Single video plans use the configured inbox directories and do not require a second confirmation.

- [ ] **Step 3: Verify failures**

```bash
rtk pytest tests/test_acquisition.py tests/test_cli_acquire.py tests/test_downloader.py -q
```

- [ ] **Step 4: Implement immutable plans and reports**

```python
@dataclass(frozen=True)
class AcquisitionPlan:
    source: str
    source_kind: SourceKind
    output_root: Path
    selected_urls: tuple[str, ...]
    selected_count: int
    language: str
    analyze: bool
    requires_confirmation: bool

@dataclass(frozen=True)
class AcquisitionReport:
    selected: int
    transcripts_ready: int
    insights_ready: int
    failures: tuple[str, ...]
```

For year filtering, use metadata discovery that returns exact `upload_date`. Do not pretend a missing date matched the filter. Report it as excluded with reason `missing_upload_date`.

- [ ] **Step 5: Implement the Click contract**

```text
yt-insights acquire SOURCE
  --slug SLUG
  --years 2025,2026
  --lang fr
  --analyze
  --dry-run
  --yes
  --json
```

`--dry-run` performs discovery but writes neither corpus files nor batch files. Without `--yes`, a multi-source command prints the plan and exits `3`. An explicit `--yes` is the only non-interactive confirmation.

In this milestone, `--analyze` uses the existing automatic backend resolver. Explicit `--backend` selection belongs to optional Task D1 and must not be exposed before that task is implemented and tested.

- [ ] **Step 6: Reuse Python services instead of invoking the old CLI**

Call `download_subtitles()`, `analyze_all()`, `Catalog.import_corpus()` and `SQLiteFtsIndex.rebuild()` through Python. Do not spawn `yt-insights run` or `runbook/run-channel.sh` from the new service. Extend `Catalog.import_corpus()` so the configured flat single-video inbox is imported alongside nested channel corpora, without double-counting either layout.

- [ ] **Step 7: Add idempotency and failure accounting**

Count cached VTT and insight files as ready. Preserve external errors with video identity. Exit `1` when every selected item fails, `4` for a partial run, and `0` for complete or cache-complete runs.

- [ ] **Step 8: Run focused and complete tests**

```bash
rtk pytest tests/test_acquisition.py tests/test_cli_acquire.py tests/test_downloader.py tests/test_catalog.py -q
rtk pytest -q
```

- [ ] **Step 9: Commit acquisition**

```bash
rtk git add src/yt_insights/acquisition.py src/yt_insights/cli_acquire.py src/yt_insights/downloader.py src/yt_insights/catalog.py tests/test_acquisition.py tests/test_cli_acquire.py tests/test_downloader.py tests/test_catalog.py
rtk git commit -m "feat: add safe corpus acquisition command"
```

### Task 5: Export source material without an LLM call

**Files:**
- Create: `src/yt_insights/exporter.py`
- Create: `src/yt_insights/cli_export.py`
- Test: `tests/test_exporter.py`
- Test: `tests/test_cli_export.py`

**Interfaces:**
- Consumes: video ID or YouTube URL, optional language and output format
- Produces: `export_video(request: VideoExportRequest, paths: DataPaths) -> ExportResult`

- [ ] **Step 1: Write failing source resolution tests**

Cover exact ID matching, `watch?v=`, `youtu.be`, one language, multiple languages without `--lang`, missing VTT, and a corrupt sidecar. Never select by title substring.

Resolution is filesystem-scoped, not a catalog query or full search-index scan. A flat inbox transcript requires a valid adjacent sidecar. The historical nested layout may derive source and title from its directory structure when no sidecar exists. Multiple candidates for the same video ID and language fail closed.

```python
def test_ambiguous_languages_require_an_explicit_choice(corpus) -> None:
    with pytest.raises(AmbiguousTranscriptLanguage) as error:
        resolve_transcript(corpus.root, "nfupYzLjFGc", language=None)

    assert error.value.languages == ("en", "fr")
```

- [ ] **Step 2: Write failing format tests**

```python
def test_markdown_export_keeps_provenance_and_timestamps(sample_vtt, tmp_path) -> None:
    result = export_video(
        VideoExportRequest("nfupYzLjFGc", "md", "fr", tmp_path / "source.md"),
        sample_vtt.paths,
    )
    body = result.path.read_text(encoding="utf-8")

    assert "https://www.youtube.com/watch?v=nfupYzLjFGc" in body
    assert "00:00:10" in body
    assert result.source_sha256 == hashlib.sha256(sample_vtt.bytes).hexdigest()
```

- [ ] **Step 3: Verify failures**

```bash
rtk pytest tests/test_exporter.py tests/test_cli_export.py -q
```

- [ ] **Step 4: Implement three renderers**

- `vtt` copies the original bytes after hash calculation.
- `txt` uses `clean_vtt()` and writes normalized UTF-8 text.
- `md` uses `parse_vtt_timestamped()` and writes metadata plus timestamped paragraphs.

Publish through `<target>.tmp` and `os.replace()`. Refuse to overwrite an existing target unless `--force` is explicit.

- [ ] **Step 5: Implement the CLI**

```text
yt-insights export video VIDEO_OR_URL --format md --lang fr --output article-source.md
```

When `--output` is absent, write under `data_root/exports/` as `<video_id>.<language>.<format>`. Metadata uses the canonical URL `https://www.youtube.com/watch?v=<video_id>`. `--json` returns the absolute export path, source hash, video ID, language and format.

- [ ] **Step 6: Run tests and commit**

```bash
rtk pytest tests/test_exporter.py tests/test_cli_export.py -q
rtk pytest -q
rtk git add src/yt_insights/exporter.py src/yt_insights/cli_export.py tests/test_exporter.py tests/test_cli_export.py
rtk git commit -m "feat: export source-backed video transcripts"
```

### Task 6: Add corpus discovery to the read-only MCP

**Files:**
- Modify: `src/yt_insights/mcp_server.py`
- Modify: `src/yt_insights/mcp_entrypoint.py`
- Modify: `src/yt_insights/catalog.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_catalog.py`

**Interfaces:**
- Consumes: absolute catalog database and search database
- Produces: `create_server(search_database, catalog_database)` with four closed-world tools

- [ ] **Step 1: Write failing tool-list and annotation tests**

```python
assert set(tool_names(server)) == {
    "list_corpora",
    "search_videos",
    "search_passages",
    "get_passage",
}
```

Assert all four tools declare `readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True` and `openWorldHint=False`.

- [ ] **Step 2: Write failing result-bound tests**

`list_corpora` returns at most 100 entries. `search_videos` accepts only `query`, `source` and `limit`, with `1 <= limit <= 20`. Neither returns absolute paths, SQL, exception repr or transcript bodies.

- [ ] **Step 3: Verify failures**

```bash
rtk pytest tests/test_mcp_server.py tests/test_catalog.py -q
```

- [ ] **Step 4: Add deterministic catalog queries**

```python
def list_corpora(self, *, limit: int = 100) -> tuple[CorpusSummary, ...]: ...

def search_videos(
    self,
    query: str,
    *,
    source: str | None = None,
    limit: int = 10,
) -> tuple[VideoSearchResult, ...]: ...
```

Sort corpus summaries by stable source slug and videos by existing FTS rank plus video ID as tie-breaker.

- [ ] **Step 5: Require both database paths at startup**

Support `YT_INSIGHTS_SEARCH_DATABASE` and `YT_INSIGHTS_CATALOG_DATABASE`. Missing or invalid databases produce one actionable startup error without falling back to a relative path when an environment variable is set.

- [ ] **Step 6: Run MCP behavior tests**

```bash
rtk pytest tests/test_mcp_server.py tests/test_catalog.py -q
rtk pytest -q
```

- [ ] **Step 7: Commit the expanded read-only surface**

```bash
rtk git add src/yt_insights/mcp_server.py src/yt_insights/mcp_entrypoint.py src/yt_insights/catalog.py tests/test_mcp_server.py tests/test_catalog.py
rtk git commit -m "feat: expose corpus discovery through MCP"
```

### Task 6.5: Register agent-facing commands in one integration change

**Owner:** Coordinator only after Tasks 2, 4, 5 and 6 pass independently.

**Files:**
- Modify: `src/yt_insights/cli.py`
- Create: `tests/test_cli_agent_commands.py`

**Interfaces:**
- Consumes: `doctor`, `acquire` and `export` Click command objects
- Produces: one root CLI with stable names and unchanged existing commands

- [ ] Add a failing command-list test covering every legacy and new command.
- [ ] Register the three command modules without moving their business logic into `cli.py`.
- [ ] Run one behavior test for each new command through the root Click runner.
- [ ] Run the complete suite and `rtk git diff --check`.
- [ ] Commit only `cli.py` and the coordinator-owned integration test.

```bash
rtk pytest tests/test_cli_agent_commands.py tests/test_cli_doctor.py tests/test_cli_acquire.py tests/test_cli_export.py -q
rtk pytest -q
rtk git add src/yt_insights/cli.py tests/test_cli_agent_commands.py
rtk git commit -m "feat: register agent-facing CLI commands"
```

### Task 7: Package and smoke-test the agent-facing runtime

**Files:**
- Modify: `pyproject.toml`
- Modify: `scripts/smoke_wheel.py`
- Modify: `tests/test_packaging.py`
- Modify: `INSTALL.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `llms.txt`

**Interfaces:**
- Consumes: clean source copy and wheel extras
- Produces: `yt-insights`, `yt-insights-mcp` and the four agent-facing commands from any directory

- [ ] **Step 1: Extend packaging tests**

Assert the wheel exposes `doctor`, `acquire`, `export`, `index`, `search` and `yt-insights-mcp`. Add a smoke invocation from a temporary directory whose parent is not the checkout.

- [ ] **Step 2: Build and install an inert wheel**

```bash
UV_CACHE_DIR=/private/tmp/yt-insights-uv-cache .venv/bin/python scripts/smoke_wheel.py --offline
```

Expected: the minimal wheel and `wheel[mcp]` pass, and the MCP lists exactly four tools.

- [ ] **Step 3: Run the complete verification matrix**

```bash
rtk pytest -q
rtk uv lock --check
UV_CACHE_DIR=/private/tmp/yt-insights-uv-cache .venv/bin/python scripts/smoke_wheel.py --offline
rtk git diff --check
```

- [ ] **Step 4: Document the operational contract**

Document the precedence of `data_root`, the acquisition confirmation rule, explicit backend choices, MCP environment variables, export formats and the absence of audio transcription.

- [ ] **Step 5: Commit packaging and documentation**

```bash
rtk git add pyproject.toml scripts/smoke_wheel.py tests/test_packaging.py INSTALL.md README.md CHANGELOG.md llms.txt
rtk git commit -m "docs: publish the agent-facing runtime contract"
```

## Runtime acceptance gate

- The complete Python suite passes.
- The wheel smoke passes outside the checkout with and without MCP.
- `doctor --json` contains no configured secret value.
- `acquire --dry-run` writes no file.
- A channel or playlist without `--yes` exits before download.
- Exported Markdown contains source identity, URL and timestamps.
- The MCP lists exactly four read-only tools and rejects unknown arguments.
- The default behavior of existing CLI commands remains covered by regression tests.
- One user TOML resolves the same corpus from two unrelated current directories.
- Optional MLX status is reported separately and cannot block this gate.
