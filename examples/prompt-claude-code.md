# Retest prompt: full pipeline (non-interactive)

Copy-paste this into a Claude Code session to run the complete `yt-insights` pipeline
on the example video. The wizard auto-detects that there's no TTY, so you drive it by
passing all parameters as flags.

---

## Setup

```bash
cd /path/to/yt-insights
uv sync --extra dev
```

Make sure CC Bridge (or Ollama, or an `ANTHROPIC_API_KEY`) is available before starting.

---

## Step 0: isolate this example

```bash
export YT_INSIGHTS_DATA_ROOT="$(mktemp -d /tmp/yt-insights-example.XXXXXX)"
mkdir -p "$YT_INSIGHTS_DATA_ROOT/transcripts"
```

This keeps existing repository output untouched and gives the run a disposable,
explicit data root.

---

## Step 1: download subtitles

`yt-insights run` downloads the VTT and caches the insight in one shot, but if you
want to separate the steps, download manually first:

```bash
uv run yt-dlp \
  --write-auto-subs \
  --sub-langs 'fr' \
  --sub-format vtt \
  --skip-download \
  --output "$YT_INSIGHTS_DATA_ROOT/transcripts/%(id)s.%(ext)s" \
  'https://www.youtube.com/watch?v=nfupYzLjFGc'
```

Expected output: `$YT_INSIGHTS_DATA_ROOT/transcripts/nfupYzLjFGc.fr.vtt`

---

## Step 2: extract insights

```bash
uv run yt-insights run 'https://www.youtube.com/watch?v=nfupYzLjFGc'
```

If the VTT already exists, add `--skip-download` to avoid a second yt-dlp call.

Expected output:

- `$YT_INSIGHTS_DATA_ROOT/insights/nfupYzLjFGc.fr.json`
- `$YT_INSIGHTS_DATA_ROOT/insights/nfupYzLjFGc.fr.md`
- `$YT_INSIGHTS_DATA_ROOT/insights/AGGREGATE_REPORT.md`

---

## Step 3: suggest Shorts

```bash
uv run yt-insights suggest-shorts
```

Expected: a ranked list of Short candidates with start/end timecodes and scores.

---

## Step 4: generate a clip (non-interactive wizard)

No TTY in Claude Code, so pass all parameters explicitly:

```bash
uv run yt-insights interactive \
  --action pipeline \
  --source 'https://www.youtube.com/watch?v=nfupYzLjFGc' \
  --duration standard \
  --platform youtube-shorts \
  --format mp4
```

The wizard will auto-pick the highest-scoring clip within the duration/platform
constraints, download it, and save it to `output/clips/`.

Alternatively, pick a specific clip from the `suggest-shorts` output and download it
directly:

```bash
uv run yt-insights generate-short nfupYzLjFGc \
  --start 00:00:56 \
  --end 00:01:43 \
  --title "hook-1200-commits" \
  --output-format mp4
```

---

## Step 5: review the outputs

```bash
ls -lh "$YT_INSIGHTS_DATA_ROOT/clips/"
cat "$YT_INSIGHTS_DATA_ROOT/insights/AGGREGATE_REPORT.md"
```

Verify: at least one `.mp4` in `$YT_INSIGHTS_DATA_ROOT/clips/`, and a populated
`AGGREGATE_REPORT.md` under `$YT_INSIGHTS_DATA_ROOT/insights/`.

---

## Available duration values

| Value | Range |
|---|---|
| `very-short` | 15-30s |
| `standard` | 30-60s |
| `long` | 60-90s |
| `any` | 0-90s (no filter) |

## Available platform values

| Value | Max duration |
|---|---|
| `youtube-shorts` | 60s |
| `tiktok` | 60s |
| `reels` | 90s |
| `linkedin` | 90s |
| `none` | no limit |
