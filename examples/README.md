# Examples

Real output from running `yt-insights` on a public YouTube video.

**Source video**: [7 mois, 1200 commits, 600 PRs, 50 releases : REX d'une équipe AI-first de 4 personnes](https://www.youtube.com/watch?v=nfupYzLjFGc) by Florian Bruniaux

---

## Files

| File | What it is |
|---|---|
| `*.fr.vtt` | Raw subtitle file downloaded by yt-dlp. Input for the analysis. |
| `*.fr.json` | Structured insight extracted by the LLM. Source of truth for the cache. |
| `*.fr.md` | Markdown rendered from the JSON. Human-readable output. |
| `prompt-claude-code.md` | Ready-to-paste prompt for running the full pipeline inside Claude Code. |

The `.json` and `.md` share the same stem as the `.vtt`, which is how cache lookup works at runtime.

---

## Reproduce this output

```bash
# Install yt-insights
pipx install yt-insights

# Download subtitles + extract insights
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc

# Output lands in:
#   output/transcripts/*.vtt   subtitle file
#   output/insights/*.json     structured insight
#   output/insights/*.md       rendered markdown
#   output/insights/AGGREGATE_REPORT.md
```

Skip the download if the VTT is already present:

```bash
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc --skip-download
```

Force re-analysis even if the cache exists:

```bash
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc --force
```

---

## Shorts pipeline

```bash
# Suggest the best Short moments from existing VTTs
yt-insights suggest-shorts

# Download the best clip as MP4
yt-insights generate-short nfupYzLjFGc \
  --start 00:00:56 --end 00:01:43 \
  --title "hook-1200-commits" \
  --output-format mp4

# Output lands in output/clips/
```

---

## Interactive wizard

In a real terminal (TTY), the wizard guides you step by step with arrow-key menus:

```bash
yt-insights interactive
```

Inside Claude Code (no TTY), pass all flags directly. See `prompt-claude-code.md` for the ready-to-use prompt.

---

## What the insight JSON contains

```json
{
  "subject":    "One-sentence description of the video topic",
  "key_points": ["3-5 main points covered"],
  "tools":      [{"name": "Tool", "context": "how it was used"}],
  "advice":     ["Immediately actionable recommendations"],
  "quotes":     ["Notable quotes from the speaker"]
}
```

LLM backend is auto-detected at runtime: cc-bridge (port 4141) first, then Ollama, then
`ANTHROPIC_API_KEY`. No configuration needed if one of these is available.
