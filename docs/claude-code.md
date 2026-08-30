# Claude Code and Codex integration

YT Insights exposes one product through three explicit surfaces: the packaged CLI, a four-tool read-only MCP server, and portable skills. The skills describe workflows and delegate execution to the CLI or MCP instead of duplicating product logic.

See [Implementation status](IMPLEMENTATION-STATUS.md) for the detailed verification matrix.

## Current status

| Surface | Repository state | Development workstation state |
| --- | --- | --- |
| Portable skills | `.agents/skills/youtube-*` contains acquire, research and export | Shared release `60cbcac...` is active; a fresh Codex session sees all three skills |
| Claude researcher | `.claude/agents/youtube-corpus-researcher.md` is versioned | Global agent not installed; fresh Claude validation is blocked because the CLI is not connected |
| Codex researcher | `.codex/agents/youtube-corpus-researcher.toml` is versioned | Global agent not installed |
| Runtime | Package and wheel smoke tests are versioned | Managed global `uv` runtime not installed |
| Setup | `yt-insights setup assistants` packages skills, agents and MCP registration | Dry-run and fake-client transaction tests pass; no new live transaction applied |
| MCP | `yt-insights-mcp` exposes four read-only tools | Global Claude/Codex MCP entries not installed |
| Implicit routing | A rejection fixture protects against accidental auto-routing | Explicit skill, agent or MCP invocation is required |

Cloning and the default setup preview never change global Claude or Codex configuration. The shared skills release was installed through a separate, approved transaction. The new `--apply` mode is the explicit user-level transaction for native agents and MCP registration; it has not been run on the development workstation.

## Install the assistant surfaces

Use one absolute corpus path from every project:

```bash
uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --dry-run

uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --apply

uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --verify --json
```

The installer copies the three portable skills to `~/.agents/skills`, installs
one read-only native researcher per selected client, and registers the same
absolute MCP executable and databases in Claude Code and Codex. A conflict
stops before any write. A failed registration rolls back the state created by
that execution. Existing different files or MCP entries require manual review.

See [assistant prompt examples](../examples/agent-prompts.md) for explicit
acquisition, research, article dossier, comparison and export requests.

## Supported portable skills

| Skill | Purpose | Writes data? |
| --- | --- | --- |
| `youtube-acquire` | Acquire a video or channel after source confirmation | Yes, through the packaged CLI |
| `youtube-research` | Search and compare the local corpus with citations | No |
| `youtube-export` | Export an existing corpus item to a requested format | Yes, only to the requested export target |

Invoke these skills explicitly. No global hook silently routes general YouTube requests to YT Insights.

## Local use from the repository

```bash
uv sync --extra mcp --extra dev
uv run yt-insights doctor --json
uv run yt-insights acquire "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run --json
uv run yt-insights index --all
uv run yt-insights search "context engineering" --limit 5
uv run yt-insights-mcp
```

Until the managed global runtime is installed, prefer `uv run`. A bare `yt-insights` command may resolve to an older pyenv shim that does not expose the current command set.

## Read-only researcher surface

The MCP server exposes exactly four tools:

| Tool | Result |
| --- | --- |
| `list_corpora` | Available local corpora and their bounded metadata |
| `search_videos` | Indexed videos matching a metadata query |
| `search_passages` | Ranked transcript passages with timestamps and provenance |
| `get_passage` | One indexed passage and its source metadata |

Research answers must preserve the source video, timestamp and distinction between direct evidence, adjacent evidence and inference. Repository assets prove that the integration can be packaged and tested. They do not prove that a global agent or MCP entry is installed on a workstation.

## Historical Claude commands

The project-specific commands remain compatibility shortcuts. They are not an implicit dispatcher.

| Command | Purpose |
| --- | --- |
| `/yt-process` | Run the processing pipeline for a known source |
| `/yt-ask` | Ask a question against existing local outputs |
| `/yt-analyze-video` | Analyze one processed video |
| `/yt-analyze-channel` | Analyze one processed channel |
| `/yt-shorts` | Generate Shorts candidates from existing insights |

## Verification boundary

```bash
.venv/bin/python -m pytest -q
UV_CACHE_DIR=/tmp/yt-insights-uv-cache bash scripts/smoke_wheel.sh
```

Automated checks cover package structure, CLI contracts, MCP schemas, packaged assets, dry-run, conflicts, rollback and verification against fake clients. Fresh client sessions are still required to prove discovery and effective loading. At the current checkpoint, only the fresh Codex portable-skill canary is confirmed. Fresh Claude loading, global native agents and global MCP registration remain `UNKNOWN` because the new transaction has not been applied.
