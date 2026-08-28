---
name: youtube-research
description: Search and compare the local yt-insights YouTube corpus with source-backed passages, timestamps, channels, and video metadata. Use for trouve dans mon corpus, cherche cette citation, compare ces chaines, or find the source video. Do not use to download videos or modify the corpus.
---

# Research the local YouTube corpus

Use only the read-only `yt-insights` MCP surface. Do not mutate, rebuild, acquire, or export corpus data.

## Tool order

Use the narrowest useful sequence and stop when it answers the request:

1. `list_corpora` to establish available sources and coverage when the target corpus is unclear.
2. `search_videos` to identify candidate videos by title, metadata, or source.
3. `search_passages` to retrieve relevant transcript excerpts, optionally bounded by channel or language.
4. `get_passage` to resolve a selected passage to its full source-backed detail.

For each supported claim, return the video title, channel, timestamped URL, language when relevant, and a bounded excerpt. Distinguish direct evidence from comparison or interpretation. State the query, filters, result limit, truncated results, and corpus coverage limits that affect the answer.

If the index returns no evidence, say so. Do not invent a match, infer unavailable transcript content, or present a nearby result as proof. Acquisition and export require a different workflow in the main session.
