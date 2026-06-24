# Examples

Real output from running `yt-insights` on a public YouTube video.

**Source video**: [7 mois, 1200 commits, 600 PRs, 50 releases : REX d'une équipe AI-first de 4 personnes](https://www.youtube.com/watch?v=nfupYzLjFGc) by Florian Bruniaux

---

## Files

| File | What it is |
|---|---|
| `*.fr.vtt` | Raw subtitle file downloaded by yt-dlp. This is the input. |
| `*.fr.json` | Structured insight extracted by the LLM. This is the source of truth. |
| `*.fr.md` | Markdown rendered from the JSON. This is the readable output. |

The `.json` and `.md` share the same stem as the `.vtt`, which is how the cache lookup works at runtime.

---

## Reproduce this output

```bash
# Install yt-insights
pipx install yt-insights

# Download subtitles for the video
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc

# Output lands in:
#   yt_transcripts/*.vtt   (subtitle file)
#   yt_insights/*.json     (structured insight)
#   yt_insights/*.md       (rendered markdown)
#   yt_insights/AGGREGATE_REPORT.md
```

To skip the download and analyze an existing VTT:

```bash
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc --skip-download
```

To force re-analysis even if the cache exists:

```bash
yt-insights run https://www.youtube.com/watch?v=nfupYzLjFGc --force
```

---

## What the JSON contains

```json
{
  "subject":     "One-sentence description of the video topic",
  "key_points":  ["3-5 main points covered"],
  "tools":       [{"name": "Tool", "context": "how it was used"}],
  "advice":      ["Immediately actionable recommendations"],
  "quotes":      ["Notable quotes from the speaker"]
}
```

The LLM backend is auto-detected at runtime: cc-bridge (port 4141) first, then Ollama, then `ANTHROPIC_API_KEY`. No configuration needed if one of these is available.
