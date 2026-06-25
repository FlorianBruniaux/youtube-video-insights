# yt-insights

[![PyPI](https://img.shields.io/pypi/v/yt-insights)](https://pypi.org/project/yt-insights/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Point it at a YouTube channel. Get a structured JSON + Markdown insight file per video, and an aggregate report synthesising patterns across the full channel.

---

## TLDR

- One command: `yt-insights run https://www.youtube.com/@ChannelName` → per-video insights + aggregate report
- 5-key insight schema per video: subject, key points, tools mentioned, actionable advice, notable quotes
- LLM backend auto-detected at runtime: cc-bridge (port 4141) → Ollama → Anthropic API → MLX
- JSON is the source of truth, Markdown is rendered from it. Atomic writes, no corrupt files on Ctrl-C
- Idempotent: already-analyzed videos are cached, `--force` re-processes everything

---

## What it's actually for

Point it at any YouTube channel and get two things back. First, a structured insight file per video: subject, key takeaways, tools mentioned, actionable advice. On top of that, one aggregate report that synthesises patterns across the whole channel, so you see the editorial logic, recurring angles, what gets covered a lot and what doesn't.

The second pipeline is about Shorts. The same transcripts go through a scorer that finds the top 3 moments per video, 30-90 seconds each, with verbatim text and precise timestamps. Pick the one you want, run one more command, and yt-dlp downloads just that segment rather than the full video.

Practically speaking: you can audit a competitor channel in 10 minutes and know their full editorial strategy. Run it across several channels on the same topic and you'll find the angles nobody is covering yet. The exported JSON and VTT files are also ready for RAG pipelines, fine-tuning datasets, or anything else that needs clean text from video.

---

## How it works

### Insight pipeline

```
YouTube URL / channel
        │
        ▼
   yt-dlp (subprocess)          Downloads auto-generated subtitles
        │
        ▼
   yt_transcripts/*.vtt
        │
        ▼
   cleaner.py                   Deduplicates lines, strips timestamps
        │                       and HTML tags from VTT format
        ▼
   analyzer.py ─────────────►  LLM backend
   ThreadPoolExecutor           cc-bridge │ Ollama │ Anthropic API │ MLX
   (3× remote, 1× local)        auto-detected at first call
        │
        ├──► yt_insights/<video>.json   ← source of truth (atomic write)
        └──► yt_insights/<video>.md     ← rendered from JSON
                │
                ▼
        reporter.py
        Counter (top tools, no LLM)
        + one LLM call for narrative synthesis
                │
                ▼
        AGGREGATE_REPORT.md + .json
```

### Shorts suggestion pipeline

```
yt_transcripts/*.vtt
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
        ├──► yt_shorts/<video>.json   ← suggestion cache (atomic write)
        ├──► yt_shorts/<video>.md     ← human-readable suggestions
        └──► yt_shorts/INDEX.md       ← global index sorted by score
                                         across all talks
        │ (optional phase 2)
        ▼
   generate-short command
   yt-dlp --download-sections   Downloads only the segment (~20-50MB,
                                not the full video)
        │
        ▼
   yt_shorts_clips/<title>.mp4
```

**Key design decisions:**

- yt-dlp runs as a subprocess, never imported as a library (subprocess is the stable contract)
- `stop_reason == "max_tokens"` gates writes: truncated responses are never cached, retried on next run
- `ThreadPoolExecutor` over asyncio: `httpx.Client` is thread-safe, no event loop needed
- YouTube VTT rolling captions repeat each phrase 2-3x as it scrolls; `vtt_parser.py` tracks first occurrence per unique text fragment, giving clean timestamped segments

---

## Prerequisites

- Python 3.11 or later
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) installed in PATH (`brew install yt-dlp` or `pip install yt-dlp`)
- At least one LLM backend (see [Backends](#backends))

---

## Installation

```bash
pipx install yt-insights
```

With Apple Silicon MLX support:

```bash
pipx install "yt-insights[mlx]"
```

For development:

```bash
git clone https://github.com/FlorianBruniaux/yt-insights
cd yt-insights
pip install -e .
```

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
yt-insights suggest-shorts --vtt yt_transcripts/20260423-talk.vtt

# Regenerate the global Shorts index (no LLM call)
yt-insights suggest-shorts --index-only

# Download a specific clip segment (no full-video download)
yt-insights generate-short VIDEO_ID --start 00:05:10 --end 00:05:55 --title "hook-context-engineering"
```

Expected output:

```
Downloading subtitles from https://www.youtube.com/@DevWithAIYoutube ...
  47 subtitle file(s) downloaded.

Analyzing 47 video(s) with model 'claude-haiku-4-5' ...
  47 insight(s) ready in yt_insights/

Generating aggregate report ...
  Report written to yt_insights/AGGREGATE_REPORT.md
Done.
```

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

yt-insights auto-detects the first available backend at runtime. Detection order:

| Priority | Backend | How to activate | Model format |
|---|---|---|---|
| 1 | cc-bridge | Start cc-bridge on port 4141 | `anthropic/github_copilot/gpt-5-mini` |
| 2 | Ollama | `ollama serve` | `llama3.2:latest` (first llama/qwen model found) |
| 3 | Anthropic API | `export ANTHROPIC_API_KEY=sk-...` | `claude-haiku-4-5` |
| 4 | Any OpenAI-compatible | `--base-url http://localhost:8000/v1` | provider-specific |
| 5 | MLX (Apple Silicon) | `pip install yt-insights[mlx]` + `--base-url mlx` | model path |

Override model and endpoint via flags:

```bash
yt-insights run <url> --model claude-sonnet-4-6 --base-url https://api.anthropic.com/v1
```

**cc-bridge model ID gotcha**: use the gateway format `anthropic/{provider}/{model}` (e.g. `anthropic/github_copilot/gpt-5-mini`) to route directly to the named provider via cc-bridge's stored credentials. A plain model ID (e.g. `claude-haiku-4-5`) uses cc-bridge's `active_route` and may return 401 if active_route is set to Anthropic in OAuth passthrough mode.

---

## CLI reference

```
yt-insights run SOURCE [OPTIONS]

  SOURCE  YouTube channel, playlist, video URL, or local file with one URL per line.

  --skip-download           Skip yt-dlp, use existing VTT files in yt_transcripts/
  --force                   Re-analyze even if insight cache exists
  --model TEXT              Override LLM model
  --base-url TEXT           Override LLM API base URL
  --concurrency INTEGER     Max parallel LLM calls (0 = auto: 3 for API, 1 for Ollama/MLX)
  --output-dir PATH         Base directory for yt_transcripts/ and yt_insights/
  --sleep-requests INTEGER  Seconds to wait between yt-dlp requests (rate limiting)

yt-insights report [OPTIONS]

  --output PATH    Output path (default: yt_insights/AGGREGATE_REPORT.md)
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
  --output-dir PATH  Base directory (expects yt_transcripts/, yt_insights/, yt_shorts/)

yt-insights generate-short VIDEO_ID [OPTIONS]

  Download a single clip segment from YouTube using yt-dlp --download-sections.
  Only the requested range is fetched (~20-50MB), not the full video.

  --start TEXT       Start timestamp HH:MM:SS  [required]
  --end TEXT         End timestamp HH:MM:SS  [required]
  --title TEXT       Short title for output filename
  --output-dir PATH  Directory for clip output (default: yt_shorts_clips/)

yt-insights config show [OPTIONS]

  Print the resolved configuration (active values after merging all layers).
  Shows which source each value comes from: default, config.toml, env var, or CLI flag.
  Accepts --model and --base-url to simulate overrides before running.

yt-insights config init

  Create ~/.config/yt-insights/config.toml with all defaults commented.
```

---

## Output structure

```
yt_transcripts/
  20260101 - Video Title [videoID].fr.vtt   # raw subtitles (gitignored)

yt_insights/
  20260101 - Video Title [videoID].fr.json  # source of truth
  20260101 - Video Title [videoID].fr.md    # rendered from JSON
  AGGREGATE_REPORT.md                       # narrative synthesis
  AGGREGATE_REPORT.json                     # top tools + per-video index

yt_shorts/
  20260101 - Video Title [videoID].fr.json  # suggestion cache (atomic write)
  20260101 - Video Title [videoID].fr.md    # human-readable: timestamps, hook, score, verbatim
  INDEX.md                                  # global table sorted by score across all talks

yt_shorts_clips/
  talk-title_000510.mp4                     # downloaded segment (generate-short output)
```

Example `yt_shorts/video.md` entry:

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

---

## Idempotence

Every run is idempotent. If `video.json` already exists for a given VTT file, it is loaded from disk with no LLM call. Interrupt the process at any point: partial runs leave `.tmp.json` orphans at worst, never a corrupt `.json`.

Re-process everything from scratch:

```bash
yt-insights run <url> --force
```

---

## Insight JSON schema

The LLM is always instructed to return exactly this structure:

```
subject      string        One-sentence description of the video topic
key_points   string[]      3-5 main points covered
tools        object[]      {name, context}: tools and technologies mentioned
advice       string[]      Immediately actionable recommendations
quotes       string[]      Notable quotes (empty array if none)
```

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
| Concurrency | 3 threads for remote APIs, 1 for Ollama/MLX (auto-tuned) |
| Backends | cc-bridge, Ollama, Anthropic API, any OpenAI-compat, MLX |
| Auto-detection | Backend probed at first LLM call, no config needed |
| Aggregate report | `Counter` top tools (no LLM) + one narrative LLM call |
| Config file | 4-layer merge: defaults → TOML → env vars → CLI flags |
| Idempotence | Re-run safely at any time, skips existing insights |
| Shorts suggestions | Top 3 moments per talk (30-90s), scored 1-5 by LLM, cross-talk INDEX.md |
| Timestamped VTT | First-occurrence dedup with timestamps preserved for Shorts pipeline |
| Clip download | `yt-dlp --download-sections`, segment only, no full-video fetch |

---

## For AI coding assistants

`docs/machine-readable/` contains five structured reference files designed to be loaded into an AI agent's context:

| File | What it covers |
|------|---------------|
| `llms.txt` | Full quick reference: module map, constraints, env vars, decision tree |
| `code-map.yaml` | Every module, its exports, dedup strategies, data directories |
| `adr-index.yaml` | 10 Architecture Decision Records with rationale |
| `constraints.yaml` | Forbidden patterns, required patterns, open tensions |
| `tech-decisions.yaml` | Technology stack choices per concern (VTT parsing, storage, concurrency) |

Load `llms.txt` for a full context snapshot. Load individual YAML files when working on a specific area.

---

## Claude Code integration

`.claude/agents/yt-video-analyst.md` and four skills in `.claude/skills/` wrap the CLI pipeline in a conversational workflow. The agent checks caches, presents options, and waits for your input before downloading any clip.

See `docs/claude-code.md` for the full reference.

### Agent

`yt-video-analyst` dispatches automatically when you paste a YouTube URL in Claude Code. It identifies what is already cached, asks what you want (transcript, insights, Shorts, or the full pipeline), and invokes the matching skill.

### Skills

| Skill | What it does |
|---|---|
| `/yt-get-transcript` | Downloads the VTT, checks cache first, retries with browser cookies on 429 |
| `/yt-get-insights` | Runs insight analysis on an existing VTT, reads from cache when already processed |
| `/yt-get-shorts` | Suggests the top 3 Short moments, presents them for your choice, downloads the chosen clip |
| `/yt-run-pipeline` | Runs all three steps in sequence |

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

Open a PR. No CLA, no build pipeline, just `pip install -e .` and go.

---

## License

MIT
