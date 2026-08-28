---
name: youtube-acquire
description: Acquire a YouTube video, playlist, channel, or transcript into the local yt-insights corpus. Use for requests such as recuperer une video, ajouter une chaine, download a transcript, or ingest a YouTube playlist. Do not use for code changes to a video player, YouTube SEO, or read-only corpus search.
---

# Acquire YouTube source material

Use the packaged `yt-insights` CLI. Keep acquisition in the main session because it can access the network and modify the local corpus.

## Workflow

1. Run `yt-insights doctor --json`. Stop on failed required checks and report the failed check without exposing configuration values.
2. Build the preview as `yt-insights acquire SOURCE --dry-run --json`, replacing `SOURCE` with the user's source and adding only requested filters such as `--slug`, `--years`, `--lang`, or `--analyze`.
3. Summarize the preview's selected count, source kind, output target, transcript language, and whether analysis is enabled. Report exclusions and discovery errors.
4. If the source is one video and the user explicitly requested acquisition, repeat the command without `--dry-run`.
5. If the source is a channel, playlist, or batch, wait for explicit confirmation of the preview. Then repeat the same command without `--dry-run` and with `--yes`.
6. Report `selected`, `transcripts_ready`, `insights_ready`, and the number and details of items that failed. Retain the preview's output target and report it with the final counts; do not invent more specific paths than the CLI returned.

Do not replace this workflow with a downloader command or direct corpus edits. Do not add `--yes` to the preview. If the CLI reports an authentication or bot-check failure for which browser cookies are relevant, suggest a new explicit preview using `--cookies-from-browser chrome`; do not add it preemptively.

Never claim that a transcript or insight is ready from the preview alone.
