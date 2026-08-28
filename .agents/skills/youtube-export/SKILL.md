---
name: youtube-export
description: Export an existing yt-insights video transcript as Markdown, plain text, or VTT for article research and source archives. Use for exporte ce transcript, prepare la matiere de cette video, or save this video as Markdown. Do not use to search or download missing videos.
---

# Export an indexed transcript

Export only material already present in the local corpus. This workflow is deterministic and must not invoke an LLM or acquire missing material.

## Workflow

1. Resolve the requested video through the read-only MCP. If MCP is unavailable, use `yt-insights catalog search QUERY --json`. Stop if neither returns one unambiguous existing video.
2. Check the available transcript languages. Ask the user to choose `--lang` only when more than one language exists and the request does not select one.
3. Run `yt-insights export video VIDEO_OR_URL --format FORMAT --lang LANGUAGE --json`, omitting `--lang` when the transcript is unambiguous. Supported formats are `md`, `txt`, and `vtt`.
4. Return the exact output `path`, `video_id`, `language`, format, and `source_sha256` from the JSON result.

Do not overwrite an existing file unless the user explicitly authorizes `--force`. Do not acquire a missing video, edit the transcript, invent a source hash, or treat an output path as proof before the command succeeds.
