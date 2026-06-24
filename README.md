# yt-insights

[![PyPI](https://img.shields.io/pypi/v/yt-insights)](https://pypi.org/project/yt-insights/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Extract structured insights from YouTube channel transcripts using any local or cloud LLM. Downloads auto-generated subtitles via yt-dlp, cleans the VTT format, and produces per-video JSON + Markdown with subject, key points, tools mentioned, actionable advice, and notable quotes. A final aggregate report synthesises patterns across the full channel.

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

## Contributing

Open a PR. No CLA, no build pipeline, just `pip install -e .` and go.

---

## License

MIT
