# yt-insights

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Turn YouTube channels into a local, searchable research corpus: transcripts,
structured insights, SQLite/FTS5 search, reports, and Shorts.

The current implementation can index every timestamped VTT passage and expose
the same search through the CLI or two read-only MCP tools. See the
[implementation status, diagram, and test guide](docs/IMPLEMENTATION-STATUS.md).

![yt-insights workflow: YouTube channels, playlists, and videos become transcripts, structured insights, a deduplicated SQLite/FTS5 index, search results, reports, and Short suggestions.](docs/assets/yt-insights-workflow.jpg)

---

## Start here

| Goal | Command | Result |
|---|---|---|
| Analyze a channel | `yt-insights run https://www.youtube.com/@ChannelName` | Transcripts, structured insights, and an aggregate report |
| Index an existing corpus | `yt-insights catalog import-corpus ./output` | One deduplicated SQLite catalog with durable import errors |
| Build the timestamped search index | `yt-insights index --all` | A derived FTS5 index over every VTT passage |
| Find a sourced passage | `yt-insights search "AI product discovery"` | Ranked excerpts, timestamps, and direct YouTube links |
| Query from an LLM client | `yt-insights-mcp` | Read-only `search_passages` and `get_passage` tools |

Analysis uses a local or cloud LLM. Catalog import, transcript indexing, and
both search commands do not. Repeated runs reuse analysis caches and avoid
duplicating unchanged catalog artifacts.

---

## What it's actually for

Point it at a YouTube channel to keep the VTT sources locally, generate one
structured insight file per video, and build an aggregate report across the
channel. The same VTT corpus can be indexed as timestamped passages, searched
from the CLI, or queried by an LLM through MCP while preserving the source link.

The Shorts pipeline uses those transcripts to score the top three moments per
video, with verbatim text and precise timestamps. After you choose one,
`yt-dlp` downloads that segment instead of the full video.

Use it for content research, competitive monitoring, editorial analysis, or
building a searchable source library across several channels. The exported JSON
and VTT files can also feed downstream RAG or dataset workflows.

---

## How it works

### Local knowledge path

```mermaid
flowchart LR
    A[YouTube source] --> B[VTT + metadata]
    B --> C[search-v1.sqlite3]
    C --> D[yt-insights search]
    C --> E[Read-only MCP]
    D --> F[Sourced article research]
    E --> F
```

The local catalog remains a separate inventory database. The timestamped search
index derives directly from VTT files and can be rebuilt without changing them.

<details>
<summary>Insight pipeline</summary>

```
YouTube URL / channel
        │
        ▼
   yt-dlp (subprocess)          Downloads auto-generated subtitles
        │
        ▼
   output/transcripts/*.vtt
        │
        ▼
   cleaner.py                   Deduplicates lines, strips timestamps
        │                       and HTML tags from VTT format
        ▼
   analyzer.py ─────────────►  LLM backend
   ThreadPoolExecutor           cc-bridge │ Ollama │ Anthropic API
   (3× remote, 1× Ollama)       OpenAI-compatible endpoint supported
        │
        ├──► output/insights/<video>.json   ← source of truth (atomic write)
        └──► output/insights/<video>.md     ← rendered from JSON
                │
                ▼
        reporter.py
        Counter (top tools, no LLM)
        + one LLM call for narrative synthesis
                │
                ▼
        AGGREGATE_REPORT.md + .json
```

</details>

<details>
<summary>Shorts suggestion pipeline</summary>

```
output/transcripts/*.vtt
        │
        ▼
   vtt_parser.py                Timestamped dedup: first-occurrence tracking
        │                       strips inline <c> tags, rolling caption dedup
        ▼
   [HH:MM:SS] text segments
        │
        ▼
   shorts.py ──────────────►   LLM backend (same auto-detection)
   ThreadPoolExecutor           Identifies top 3 moments (30-90s) per talk:
                                hook, score/5, verbatim, timestamps
        │
        ├──► output/shorts/<video>.json   ← suggestion cache (atomic write)
        ├──► output/shorts/<video>.md     ← human-readable suggestions
        └──► output/shorts/INDEX.md       ← global index sorted by score
                                         across all talks
        │ (optional phase 2)
        ▼
   generate-short command
   yt-dlp --download-sections   Downloads only the segment (~20-50MB,
                                not the full video)
        │
        ▼
   output/clips/<title>.mp4
```

</details>

<details>
<summary>Key design decisions</summary>

- yt-dlp runs as a subprocess, never imported as a library (subprocess is the stable contract)
- `stop_reason == "max_tokens"` gates writes: truncated responses are never cached, retried on next run
- Insight and Shorts generation send at most 10,000 transcript characters per LLM call. The CLI reports `USED/TOTAL` before each real call and marks truncation without printing transcript content. Cache hits do not call the model or print this line. The full timestamped VTT remains available to the FTS index; long-form LLM analysis is not chunked yet.
- `ThreadPoolExecutor` over asyncio: `httpx.Client` is thread-safe, no event loop needed
- YouTube VTT rolling captions repeat each phrase 2-3x as it scrolls; `vtt_parser.py` tracks first occurrence per unique text fragment, giving clean timestamped segments

</details>

---

## Prerequisites

- Python 3.11 or later
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) installed in PATH (`brew install yt-dlp` or `pip install yt-dlp`)
- At least one LLM backend (see [Backends](#backends))

---

## Installation

The project is not published on PyPI. Install the checked-out source with
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/FlorianBruniaux/youtube-video-insights
cd youtube-video-insights
uv sync --extra dev
uv run yt-insights --help
```

Add the optional MCP server when a local LLM client needs to query the index:

```bash
uv sync --extra mcp --extra dev
```

The versioned `uv.lock` fixes the complete application environment. See
[INSTALL.md](INSTALL.md) for a standard `venv` and editable-install alternative.

For a machine with no local LLM (no Ollama, no GPU), see [INSTALL.md](INSTALL.md): it covers every backend option step by step (Anthropic API key, Ollama, cc-bridge, any other OpenAI-compatible provider).

---

## Quick start

```bash
# Full pipeline: download subtitles + analyze + aggregate report
yt-insights run https://www.youtube.com/@DevWithAIYoutube

# Re-analyze existing VTT files (no download)
yt-insights run https://www.youtube.com/@DevWithAIYoutube --skip-download

# Regenerate the aggregate report only
yt-insights report

# Suggest Shorts from all existing VTT files
yt-insights suggest-shorts

# Suggest Shorts for a single talk
yt-insights suggest-shorts --vtt output/transcripts/20260423-talk.vtt

# Regenerate the global Shorts index (no LLM call)
yt-insights suggest-shorts --index-only

# Download a specific clip segment (no full-video download)
yt-insights generate-short VIDEO_ID --start 00:05:10 --end 00:05:55 --title "hook-context-engineering"
```

Expected output, with paths shortened to keep the example readable:

```
Resolved backend: backend=ollama endpoint=http://127.0.0.1:11434/v1 model=qwen3:8b
Downloading subtitles from https://www.youtube.com/@DevWithAIYoutube ...
  47 subtitle file(s) downloaded.

Analyzing 47 video(s) with model 'qwen3:8b' ...
Transcript input: 10000/45678 characters (truncated)
  47 insight(s) generated:
    output/insights/<video>.md

Generating aggregate report ...
Resolved backend: backend=ollama endpoint=http://127.0.0.1:11434/v1 model=qwen3:8b
  Aggregate  → output/insights/AGGREGATE_REPORT.md
  Full       → output/insights/FULL_REPORT.md
Done.
```

## Local watch catalog (SQLite)

The catalog commands turn existing outputs or newly discovered video lists into
one local, searchable database. They do not require an LLM.

```bash
# Import an existing multi-channel corpus without modifying its files
yt-insights catalog import-corpus \
  ./output

# Discover the current videos exposed by a channel/playlist through yt-dlp
yt-insights catalog discover \
  https://www.youtube.com/@PragmaticEngineer/videos

# Search title, source, insight text, and cleaned transcripts
yt-insights catalog search "AI product discovery"
yt-insights catalog search "Martin Fowler" --source pragmaticengineer --limit 5

# Inspect stable counts and every persisted collection/import error
yt-insights catalog stats
yt-insights catalog errors
yt-insights catalog errors --run-id 3
```

The default database is `output/catalog.sqlite3` (gitignored). Override it on
any catalog command with `--db PATH`. Canonical videos are unique by YouTube
video ID. Source membership and French/English artifacts remain separate, while
identical artifacts collapse by SHA-256. Every command is safe to rerun: a
second corpus import creates a new audit run but no duplicate video or artifact.

An import continues after malformed files and reports `status=partial`; use
`catalog errors` to see the exact paths and diagnostics. JSON files with the
expected keys but invalid value types are retained for search and explicitly
logged as validation errors.

`catalog discover` currently reuses the repository's unofficial `yt-dlp`
collector. Treat this as a local experimental adapter, not a compliance claim.
For a public or commercial service, review YouTube's Terms and prefer the
official YouTube Data API for search and metadata. The detailed source trade-offs
and phased architecture are in
[`docs/superpowers/specs/2026-08-26-youtube-newsletter-watch-design.md`](docs/superpowers/specs/2026-08-26-youtube-newsletter-watch-design.md).

---

## Local transcript search

The default command indexes a deterministic 50-file slice for quick validation.
Use `--all` to build the full local corpus index. Neither mode sends transcripts
to an LLM or a remote service.

```bash
# Inspect the first deterministic 50-file slice without creating a database
yt-insights index --dry-run

# Build the derived local FTS index (default: output/.search/search-v1.sqlite3)
yt-insights index

# Build the complete corpus index after a disk-space preflight
yt-insights index --all

# Build a more diverse 50-file evaluation slice
yt-insights index --selection representative

# Validate the existing index without scanning transcripts
yt-insights index --status

# Search passages, optionally narrowed to one channel or language
yt-insights search "reliable agents" --channel my-channel --lang en

# Same ranked results as deterministic JSON
yt-insights search "reliable agents" --json
```

Use `--corpus-root`, `--database`, and `--limit` to point at a local corpus or a
derived index. `index --limit` accepts only 1 through 50 in slice mode;
`search --limit` accepts 1 through 20. The VTT corpus remains read-only and the
SQLite database can be rebuilt at any time.

### MCP access for local LLM clients

Install the `mcp` extra, build an index, then configure the stdio command in the
LLM client:

```bash
uv sync --extra mcp --extra dev
uv run yt-insights index --all
```

```json
{
  "mcpServers": {
    "yt-insights": {
      "command": "uv",
      "args": ["run", "yt-insights-mcp"],
      "cwd": "/absolute/path/to/youtube-video-insights",
      "env": {
        "YT_INSIGHTS_SEARCH_DATABASE": "/absolute/path/to/youtube-video-insights/output/.search/search-v1.sqlite3"
      }
    }
  }
}
```

The server exposes two read-only tools: `search_passages` and `get_passage`.
It reads the database selected by `YT_INSIGHTS_SEARCH_DATABASE`, or
`output/.search/search-v1.sqlite3` by default.

---

## Supported sources

| Source type | Example |
|---|---|
| YouTube channel | `https://www.youtube.com/@DevWithAIYoutube` |
| YouTube channel (videos tab) | `https://www.youtube.com/@DevWithAIYoutube/videos` |
| Playlist | `https://www.youtube.com/playlist?list=PLxxx` |
| Single video | `https://www.youtube.com/watch?v=dQw4w9WgXcQ` |

Any URL accepted by yt-dlp works as SOURCE.

---

## Backends

An explicit `--base-url` or `YT_INSIGHTS_BASE_URL` has Priority 0 and
short-circuits every automatic probe. This includes the explicit Ollama endpoint.

| Priority | Backend | How to activate | Model format |
|---|---|---|---|
| 0 | Explicit endpoint | Set `--base-url` or `YT_INSIGHTS_BASE_URL` | provider-specific |

Automatic detection order when no endpoint was configured:

| Priority | Backend | How to activate | Model format |
|---|---|---|---|
| 1 | cc-bridge | Start cc-bridge on port 4141 | `anthropic/github_copilot/gpt-5-mini` |
| 2 | Ollama | `ollama serve` | exact requested model, or automatic local selection |
| 3 | Anthropic API | `export ANTHROPIC_API_KEY=sk-...` | `claude-haiku-4-5` |

Override model and endpoint via flags:

```bash
yt-insights run <url> --model claude-sonnet-4-6 --base-url https://api.anthropic.com/v1
```

After Ollama has been selected, an explicit `--model` or `YT_INSIGHTS_MODEL`
must match an installed model exactly. If it is missing, the CLI lists the
available names and the corresponding `ollama pull` command. Automatic local
selection happens only when no model was requested. Use both
`--base-url http://127.0.0.1:11434/v1` and `--model` to force Ollama. `--model`
alone keeps the normal detection order, including cc-bridge before Ollama.

**cc-bridge model ID gotcha**: use the gateway format `anthropic/{provider}/{model}` (e.g. `anthropic/github_copilot/gpt-5-mini`) to route directly to the named provider via cc-bridge's stored credentials. A plain model ID (e.g. `claude-haiku-4-5`) uses cc-bridge's `active_route`. The probe requires `/health` to return 200 and the minimal completion to return 2xx or 3xx. A 4xx, 429, or 5xx response falls back to Ollama, then Anthropic when its API key is available.

**MLX status**: `MLXBackend` (`backends/mlx.py`) is not currently wired into the auto-detection logic in `backends/__init__.py`. `--base-url mlx` does not select it. Installing the `mlx` extra does not change that limitation.

---

## CLI reference

<details>
<summary>Show all commands and options</summary>

```
yt-insights run SOURCE [OPTIONS]

  SOURCE  YouTube channel, playlist, video URL, or local file with one URL per line.

  --skip-download           Skip yt-dlp, use existing VTT files in output/transcripts/
  --force                   Re-analyze even if insight cache exists
  --model TEXT              Override LLM model
  --base-url TEXT           Override LLM API base URL
  --concurrency INTEGER     Max parallel LLM calls (0 = auto: 3 for API, 1 for Ollama)
  --output-dir PATH         Base directory for transcripts/ and insights/
  --sleep-requests INTEGER  Seconds to wait between yt-dlp requests (rate limiting)

yt-insights report [OPTIONS]

  --output PATH    Output path (default: <insights_dir>/AGGREGATE_REPORT.md)
  --model TEXT
  --base-url TEXT

yt-insights suggest-shorts [OPTIONS]

  Identify the top 3 Short-worthy moments (30-90s) in each VTT transcript.
  LLM criteria: autonomous hook, punchy verbatim, clean in/out points, score 1-5.
  Skips already-processed talks unless --force is set.

  --vtt PATH         Process a single VTT file instead of the full transcripts dir
  --force            Re-analyze even if suggestion cache exists
  --index-only       Regenerate INDEX.md only, no LLM calls
  --model TEXT       Override LLM model
  --base-url TEXT    Override LLM API base URL
  --output-dir PATH  Base output directory (default: output/)

yt-insights generate-short VIDEO_ID [OPTIONS]

  Download a single clip segment from YouTube using yt-dlp --download-sections.
  Only the requested range is fetched (~20-50MB), not the full video.

  --start TEXT       Start timestamp HH:MM:SS  [required]
  --end TEXT         End timestamp HH:MM:SS  [required]
  --title TEXT       Short title for output filename
  --output-dir PATH  Directory for clip output (default: output/clips/)
  --output-format TEXT  Container format: mp4, webm, or mkv

yt-insights config show [OPTIONS]

  Print effective values and sources without probing or resolving a backend.
  Endpoint diagnostics remove URL credentials, query strings, and fragments.
  Accepts --model and --base-url to simulate overrides before running.

yt-insights config init

  Create ~/.config/yt-insights/config.toml with all defaults commented.

yt-insights index [--dry-run|--status|--all]

  Build or validate the timestamped SQLite transcript index.

yt-insights search QUERY [--channel ID] [--lang CODE] [--limit 1..20] [--json]

  Search timestamped passages in the derived transcript index.
```

</details>

---

## Output structure

<details>
<summary>Show directory layout and example files</summary>

```
output/
  transcripts/
    20260101 - Video Title [videoID].fr.vtt   # raw subtitles
    20260101 - Video Title [videoID].info.json # channel metadata sidecar
  insights/
    20260101 - Video Title [videoID].fr.json  # source of truth
    20260101 - Video Title [videoID].fr.md    # rendered from JSON
    AGGREGATE_REPORT.md                       # narrative synthesis
    AGGREGATE_REPORT.json                     # top tools + per-video index
  shorts/
    20260101 - Video Title [videoID].fr.json  # suggestion cache
    20260101 - Video Title [videoID].fr.md    # timestamps, hook, score, verbatim
    INDEX.md                                  # table sorted across talks
  clips/
    talk-title_000510.mp4                     # downloaded segment
  catalog.sqlite3                             # inventory database
  .search/search-v1.sqlite3                   # timestamped passage index
```

Full-channel automation may place the same `transcripts/`, `insights/`, and
`shorts/` subdirectories under `output/<channel-slug>/`. The corpus scanner
supports both layouts. Flat layouts require the adjacent `.info.json` sidecar
to preserve channel identity.

Example `output/shorts/video.md` entry:

```markdown
## Short 1 — Score : 5/5

**Timestamps :** 00:05:10 -> 00:05:48 (38s)
**Lien direct :** https://youtube.com/watch?v=VIDEO_ID&t=310s
**Hook :** L'IA ne remplace pas le dev, elle remplace le flou
**Rationale :** Formule autonome, tension forte, borne nette sur une chute.

> "Le vrai problème c'est pas le code, c'est la spec. Et ça, l'IA ne peut pas
> l'inventer à votre place."
```

Example `video.json`:

```json
{
  "subject": "How to run local LLMs on consumer hardware",
  "key_points": [
    "RAM and VRAM constraints determine which models are viable",
    "Quantisation (4-bit/8-bit) cuts memory use with minimal quality loss",
    "Instruction-tuned models outperform base models for chat/code tasks"
  ],
  "tools": [
    {"name": "Ollama", "context": "recommended runtime for local deployment"},
    {"name": "Hugging Face", "context": "source for model cards and downloads"}
  ],
  "advice": [
    "Check your VRAM first, then pick the largest model that fits",
    "Read the model card before downloading (usage restrictions vary)"
  ],
  "quotes": [
    "The best model is the one that actually runs on your machine."
  ]
}
```

</details>

---

## Idempotence

Every run is idempotent. If `video.json` already exists for a given VTT file, it is loaded from disk with no LLM call. Interrupt the process at any point: partial runs leave `.tmp.json` orphans at worst, never a corrupt `.json`.

Re-process everything from scratch:

```bash
yt-insights run <url> --force
```

---

## Insight JSON schema

<details>
<summary>Show schema</summary>

The LLM is always instructed to return exactly this structure:

```
subject      string        One-sentence description of the video topic
key_points   string[]      3-5 main points covered
tools        object[]      {name, context}: tools and technologies mentioned
advice       string[]      Immediately actionable recommendations
quotes       string[]      Notable quotes (empty array if none)
```

</details>

---

## Configuration file

```bash
yt-insights config init  # creates ~/.config/yt-insights/config.toml
```

All keys are optional. CLI flags and `YT_INSIGHTS_*` env vars take precedence over the file.

---

## Feature summary

| Feature | Detail |
|---|---|
| Subtitle download | yt-dlp subprocess, any URL it accepts |
| VTT cleaning | Dedup, strip timestamps, HTML tags, `[Musique]` annotations |
| Insight extraction | 5-key JSON schema: subject, key_points, tools, advice, quotes |
| Atomic writes | `.tmp.json` → `os.replace()`, no corrupt files on Ctrl-C |
| Truncation guard | `stop_reason == "max_tokens"` → skip cache, retry next run |
| Caching | Cache hit = zero LLM calls, `--force` to override |
| Concurrency | 3 threads for remote APIs, 1 for Ollama (auto-tuned) |
| Backends | cc-bridge, Ollama, Anthropic API, any OpenAI-compatible endpoint |
| Auto-detection | Backend probed at first LLM call, no config needed |
| Aggregate report | `Counter` top tools (no LLM) + one narrative LLM call |
| Config file | 4-layer merge: defaults → TOML → env vars → CLI flags |
| Idempotence | Re-run safely at any time, skips existing insights |
| Shorts suggestions | Top 3 moments per talk (30-90s), scored 1-5 by LLM, cross-talk INDEX.md |
| Timestamped VTT | First-occurrence dedup with timestamps preserved for Shorts pipeline |
| Clip download | `yt-dlp --download-sections`, segment only, no full-video fetch |
| Local catalog | SQLite storage for canonical videos, sources, transcripts, insights, runs, and errors |
| Full-text search | FTS5 across titles, sources, insight text, and cleaned transcripts |
| Timestamped passage index | Full VTT corpus in `search-v1.sqlite3`, with deterministic excerpts and YouTube links |
| MCP access | Read-only `search_passages` and `get_passage` tools over the same search service as the CLI |
| Index integrity | Generation receipt bound to the database SHA-256; cached validation invalidated by file identity and `ctime` |
| Backend identity | CLI reports the resolved backend, endpoint, and exact model without exposing URL credentials |
| LLM input visibility | CLI reports used and total transcript characters before each real generation call |

---

## For AI coding assistants

Load [`llms.txt`](llms.txt) for the tracked, machine-readable project snapshot:
commands, modules, storage model, invariants, generated data, and current gaps.
The architecture rationale and implementation sequence live under
[`docs/superpowers/`](docs/superpowers/).
The [implementation status](docs/IMPLEMENTATION-STATUS.md) separates delivered
features, conditional work, and reproducible validation commands.

### Planned portable agent integration

The current project contains Claude Code wrappers, but they still assume the
repository workflow and do not provide equivalent Codex integration. The next
implementation lot will add one absolute data root, safe acquisition and export
commands, four read-only MCP tools, three shared skills, and one native
researcher agent for each host.

| Document | Purpose |
|---|---|
| [Agent platform architecture](plans/specs/AGENT-PLATFORM.md) | Target behavior, data boundaries, skills, agents and safety rules |
| [Agent-ready runtime plan](plans/2026-08-28-09-agent-ready-runtime.md) | CLI, paths, backends, acquisition, export and MCP work |
| [Claude Code and Codex integration plan](plans/2026-08-28-10-claude-codex-global-integration.md) | Portable skills, native agents, routing evaluation and digest-bound global installation |
| [Hosted service and extension plan](plans/2026-08-28-11-hosted-extension.md) | Conditional browser and remote-access path |

These features are planned, not delivered. No global Claude Code or Codex
configuration is installed by the current repository setup.

---

## Claude Code integration

`.claude/agents/yt-video-analyst.md` and five skills in `.claude/skills/` wrap the CLI pipeline in a conversational workflow. The agent checks caches, presents options, and waits for your input before downloading any clip.

The skill table below and the example session are the full reference.

### Agent

`yt-video-analyst` dispatches automatically when you paste a YouTube URL in Claude Code. It identifies what is already cached, asks what you want (transcript, insights, Shorts, or the full pipeline), and invokes the matching skill.

### Skills

| Skill | What it does |
|---|---|
| `/yt-get-transcript` | Downloads the VTT, checks cache first, retries with browser cookies on 429 |
| `/yt-get-insights` | Runs insight analysis on an existing VTT, reads from cache when already processed |
| `/yt-get-shorts` | Suggests the top 3 Short moments, presents them for your choice, downloads the chosen clip |
| `/yt-run-pipeline` | Runs transcript, insights and Shorts selection in sequence for a single video |
| `/yt-add-channel` | Processes a channel into `output/<slug>/`, rebuilds the Markdown/YAML indexes, then refreshes the SQLite catalog |

### How it works

1. You paste a YouTube URL in Claude Code
2. Agent checks `output/transcripts/`, `output/insights/`, `output/shorts/` for existing cache
3. Agent asks what you want: transcript, insights, Shorts, or everything
4. For Shorts: shows all 3 suggestions with hook, timestamps, and verbatim before asking your choice
5. Clip download starts only after you confirm the moment

Each skill respects the same idempotence as the CLI: a VTT already on disk is not re-downloaded, a cached insight JSON triggers no LLM call.

### Example session

Paste a URL in Claude Code and the agent takes over:

```
You:   https://www.youtube.com/watch?v=nfupYzLjFGc

Agent: VIDEO_ID = nfupYzLjFGc
       Cache: no transcript, no insights, no shorts yet.
       What do you want? (transcript / insights / shorts / everything)

You:   everything

Agent: [/yt-get-transcript] → output/transcripts/1200-commits-600-PRs.fr.vtt (42KB)

       [/yt-get-insights]
       Subject: 7 months solo, 1200 commits, 600 PRs, 50 releases
       Key points: solo dev velocity, AI-assisted release pipeline, PR review automation...
       Notable quote: "Je pensais que l'IA allait coder à ma place. Elle m'a surtout appris à
       mieux découper le travail."

       [/yt-get-shorts]
       Option 1 | Score 5/5 | 00:00:56 → 00:01:45 (49s)
       Hook: "1 200 commits, 600 PRs en 7 mois, seul dev"
       Verbatim: "Les chiffres c'est 7 mois à peu près, 1 200 commits..."

       Option 2 | Score 5/5 | 00:03:47 → 00:04:47 (60s)
       Hook: "Une plateforme de zéro en 5 semaines, seul"
       Verbatim: "La première version était en prod au bout de 5 semaines..."

       Option 3 | Score 4/5 | 00:07:12 → 00:08:05 (53s)
       Hook: "L'IA ne code pas à ta place, elle t'oblige à mieux découper"
       Verbatim: "Ce que j'ai découvert c'est que le vrai gain..."

       Which option? (1/2/3 or custom timestamps)

You:   2

Agent: Downloading segment 00:03:47 → 00:04:47...
       Clip saved: output/clips/plateforme-5-semaines-seul_000347.mp4 (1.8MB)
```

Run the pipeline on any video, skip any step you have already done. The agent never re-downloads a cached file or re-runs an LLM call unless you ask it to.

---

## Contributing

Open a PR. No CLA is required. Run `uv sync --extra mcp --extra dev`, then the
following checks before submitting a change:

```bash
uv run --extra mcp --extra dev pytest -q
uv lock --check
git diff --check
```

---

## License

MIT
