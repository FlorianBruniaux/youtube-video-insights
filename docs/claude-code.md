# Claude Code and Codex integration

YT Insights exposes one product through a packaged CLI, a four-tool read-only
MCP server, and four portable skills. Product state transitions remain in the
CLI. Skills explain the workflow and never reproduce its SQL, acquisition, or
index-publication logic.

## Current boundary

| Surface | Repository | Live workstation |
|---|---|---|
| Portable skills | Four: acquire, read-only research, export, cumulative research | Older shared release has three; cumulative skill not globally installed |
| Native researchers | Claude Code and Codex read-only agents are versioned | Global installation not verified |
| Runtime | Wheel and offline smoke are versioned | Managed global runtime not verified |
| MCP | Exactly four read-only tools | Global client registrations not verified |
| Fresh clients | Static assets and routing fixtures are tested | Claude Code `UNKNOWN`, project-local Codex `PASS` |
| Global activation | Candidate workflow exists | `false` |

Cloning, installing the project, and the default setup preview do not change
global Claude Code or Codex configuration.

## Four portable skills

| Skill | Purpose | Writes? |
|---|---|---|
| `youtube-acquire` | Preview and acquire a video, playlist, channel, or batch | Yes, after the CLI confirmation boundary |
| `youtube-research` | Search the existing catalogue and timestamped passages | No |
| `youtube-export` | Export an existing transcript as VTT, text, or Markdown | Only the requested destination |
| `youtube-cumulative-research` | Assess, ask, discover, ask again, acquire exact IDs, reassess, and optionally export a dossier | Yes, only after explicit decisions |

The cumulative skill runs in the main session. It never uses the read-only MCP
for mutation and never approves candidates on behalf of the user.

## Mandatory cumulative workflow

1. Run `yt-insights research start ... --json` or resume with `status`.
2. Present coverage, freshness, newest source date, last discovery date, and
   coverage limits.
3. Ask whether the evidence is sufficient whenever
   `required_user_action=confirm_sufficiency_or_refresh`.
4. If the user requests a refresh, record `decide ... refresh`, then run
   `discover`. Present at most ten candidates.
5. Ask the user to choose one to five exact IDs or cancel.
6. Run `approve` with unchanged IDs, then `acquire`. Reassess and ask the
   sufficiency question again.
7. After sufficiency is confirmed, ask whether the user wants a dossier, an
   article draft, a corpus export, both, or nothing else.

Every mutation uses the latest returned revision and a fresh idempotency key.
`status --json` exposes `acquisition_history`, bounded to the latest 100
attempts, plus `acquisition_history_truncated`. Each attempt contains
`attempt_id`, `status`, and `items`; each item contains `video_id`, `status`,
`error_code`, and `source_sha256`. Idempotency keys, cookie selectors,
transcripts, and raw diagnostics are not exposed. A partial-batch retry resumes
only items recorded as `failed_retryable`; it does not reacquire any item with
a recorded terminal outcome.

An invalid JSON response, stale revision, unavailable network, or unknown
session fails closed.

English prompts for the three pilot topics, resume, refresh, exact candidate
approval, dossier export, and current-project copy are in
[examples/agent-prompts.md](../examples/agent-prompts.md).

## Install assistant assets

Preview the complete skills, native agents, and MCP transaction:

```bash
export YT_INSIGHTS_DATA_ROOT="/absolute/path/to/yt-insights-data"
uv run --extra mcp yt-insights setup assistants \
  --client both \
  --data-root "$YT_INSIGHTS_DATA_ROOT" \
  --dry-run
```

`YT_INSIGHTS_DATA_ROOT` must resolve to the absolute YT Insights data root used
by both clients. Replace the placeholder before running the command; do not
derive this path from the current project directory.

`--apply` is an explicit user-level write. `--verify` checks the managed files
and registrations. Different existing files or MCP entries block the
transaction instead of being overwritten.

To install or upgrade only the four skills and native agent assets without
reading or changing MCP registrations:

```bash
uv run yt-insights setup assistants --client both --assets-only --dry-run
uv run yt-insights setup assistants --client both --assets-only --apply
uv run yt-insights setup assistants --client both --assets-only --verify
```

No global apply is authorized by this documentation. A live change requires an
inert candidate, preimage hashes, an exact redacted diff, a digest-bound
approval, a preimage recheck, rollback, and fresh-client canaries.

## Read-only MCP

| Tool | Result |
|---|---|
| `list_corpora` | Available local corpora and bounded metadata |
| `search_videos` | Catalogue videos matching a metadata query |
| `search_passages` | Ranked transcript passages with timestamps and provenance |
| `get_passage` | One passage and its source metadata |

The MCP does not discover YouTube candidates, acquire videos, rebuild indexes,
write dossiers, expose SQL, or run a shell.

## Verification boundary

```bash
uv run --extra mcp --extra dev pytest -q
.venv/bin/python scripts/smoke_wheel.py --offline
git diff --check
```

The wheel smoke installs assets under a temporary HOME, checks all ten
`research` commands, and fails if fake Claude or Codex executables are invoked
by assets-only setup. It does not prove a fresh real client loads the skill.

Observed external gates at this checkpoint:

- relevance: `UNKNOWN`;
- discovery probe: `PASS`, 3 subjects with 10 candidates each;
- refresh performance: `PASS`, 5 builds, p95 `47.122951 s`;
- live YouTube cumulative flow: `UNKNOWN`;
- fresh Claude Code session: `UNKNOWN`, CLI not authenticated;
- fresh project-local Codex session: `PASS`, skill and approval boundaries loaded;
- global activation: `false`.

Local validation passed `844` tests plus `10` subtests, full Ruff, Mypy on the
44 source files, and `git diff --check`. This is not a `mypy --strict` claim.
Hosted GitHub Actions
[run 33414788777](https://github.com/FlorianBruniaux/youtube-video-insights/actions/runs/33414788777)
passed on `000e9b4`, including Python 3.11, Python 3.12, and packaging/runtime.
This hosted result validates that SHA only, not later commits. The detailed
fresh-client evidence is in
[the canary receipt](../plans/evidence/2026-08-31-fresh-client-canaries.md).
