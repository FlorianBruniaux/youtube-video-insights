# Claude Code and Codex prompts

Name the skill or agent explicitly in each prompt. YT Insights keeps routing explicit so that a general YouTube request cannot trigger local acquisition or corpus research by mistake.

## Research AI workflows in product and engineering teams

```text
Use youtube-cumulative-research to build a source-backed research session about
the state of the art in AI workflows for product and engineering teams. Start
with this exact command:
`yt-insights research start "AI workflows in product and engineering teams" --query "AI product engineering team workflows" --query "AI adoption product development teams" --freshness-profile standard --json`
Check the local catalogue before proposing any YouTube discovery. Report
coverage, freshness, source timestamps, and explicit coverage limits. Whenever
the workflow asks whether the evidence is sufficient, ask me before continuing.
```

## Research cost-efficient local inference with MLX and Ollama

```text
Use youtube-cumulative-research to build a source-backed research session about
maximizing local AI inference with MLX and Ollama while controlling cloud API
costs. Start with this exact command:
`yt-insights research start "Cost-efficient local AI inference" --query "local LLM inference cost MLX Ollama" --freshness-profile standard --json`
Check the local catalogue first. Separate measured runtime claims from opinions,
include source timestamps, and state explicit coverage limits. Ask me whether
the current evidence is sufficient before any YouTube discovery or acquisition.
```

## Research AI-assisted code quality

```text
Use youtube-cumulative-research to build a source-backed research session about
using AI to improve code quality through rules, tests, review, and verification.
Start with this exact command:
`yt-insights research start "AI-assisted code quality" --query "AI code quality rules testing code review" --freshness-profile standard --json`
Check the local catalogue first. Preserve source timestamps, distinguish
evidence from recommendations, and state explicit coverage limits. Ask me
whether the evidence is sufficient before looking for newer YouTube sources.
```

## Resume a cumulative research session

```text
Use youtube-cumulative-research to resume SESSION_ID. Run this exact command:
`yt-insights research status SESSION_ID --json`
Preserve its latest revision exactly, then summarize the recorded queries,
freshness, newest relevant source date, last successful discovery date, source
timestamps, and explicit coverage limits. Follow the returned
required_user_action literally and ask me before any mutation.
```

## Refresh stale evidence and review candidates

```text
Use youtube-cumulative-research for SESSION_ID. First run:
`yt-insights research status SESSION_ID --json`
Show the source timestamps and explicit coverage limits. If I confirm that I
want newer evidence, run these exact commands with the latest returned revision:
`yt-insights research decide SESSION_ID refresh --revision REVISION --idempotency-key KEY --json`
`yt-insights research discover SESSION_ID --revision REVISION --json`
`yt-insights research candidates SESSION_ID --json`
Present at most ten candidates with their exact IDs, dates, channels, matched
queries, and URLs. Do not approve or acquire any candidate until I choose the
IDs.
```

## Approve exact candidate IDs

```text
Use youtube-cumulative-research to continue SESSION_ID with only VIDEO_ID_A
VIDEO_ID_B. Recheck the latest session revision, then run:
`yt-insights research approve SESSION_ID VIDEO_ID_A VIDEO_ID_B --revision REVISION --idempotency-key KEY --json`
Use the new revision from that approval response and acquire only those unchanged
IDs with:
`yt-insights research acquire SESSION_ID --revision APPROVAL_REVISION --idempotency-key NEW_KEY --json`
Never substitute related videos. After reassessment, report the source
timestamps and explicit coverage limits, then ask again whether the evidence is
sufficient.
```

## Export a deterministic research dossier

```text
Use youtube-cumulative-research to resume SESSION_ID and verify that sufficiency
has been confirmed. Then run:
`yt-insights research export SESSION_ID --output /absolute/path/to/research-dossier --json`
Do not draft an article unless I ask separately. Report the exact dossier path
and manifest hash, retain source timestamps, and state explicit coverage limits
and unresolved questions.
```

## Copy a dossier into the current project

```text
Use youtube-cumulative-research to resume SESSION_ID and verify that sufficiency
has been confirmed. Export a deterministic copy into this project with:
`yt-insights research export SESSION_ID --output "$PWD/research/yt-insights/SESSION_ID" --json`
Do not alter the source corpus. Report the exact copied paths, source timestamps,
manifest hash, and explicit coverage limits so the project can review the
evidence independently.
```

## Preview a channel before acquisition

```text
Use youtube-acquire to preview this YouTube channel: URL.
Do not download anything before I confirm. Report the number of selected videos,
the exclusions, the requested language, and the target directory.
```

## Acquire one specific video

```text
Use youtube-acquire to add this video to my local corpus: URL.
Start with doctor and a dry-run. If the video is identified correctly and all
required checks pass, run the acquisition. Then report selected,
transcripts_ready, insights_ready, and any failures.
```

## Find evidence for an article

```text
Use youtube-research to search my corpus for passages that answer this question:
QUESTION. Return at most 10 passages. For each result, include the title,
channel, bounded excerpt, timestamp, direct link, and language. Separate direct
evidence from your interpretation and state the corpus coverage limits.
```

## Compare two channels

```text
Use youtube-corpus-researcher to compare the positions of CHANNEL_A and
CHANNEL_B on TOPIC. Find agreements, disagreements, and concrete examples.
Every claim must link to a timestamped passage. List the questions that the
corpus cannot resolve.
```

## Prepare an article research pack

```text
Use youtube-corpus-researcher to prepare a source-backed research pack about
TOPIC for a blog article. Group recurring ideas, disagreements, examples, and
useful quotations. For every item, retain the title, channel, excerpt,
timestamp, and YouTube link. Finish with the angles that still lack evidence
in the corpus.
```

## Find the source of a quotation

```text
Use youtube-research to find the source of this sentence or idea in my corpus:
EXCERPT. Return only supported matches with the passage, timestamp, and direct
link. If the search finds no evidence, say so without presenting a nearby
result as proof.
```

## Export material from one video

```text
Use youtube-export to export VIDEO_URL_OR_ID as Markdown. Do not call an LLM or
overwrite an existing file. Return the exact path, language, format, and
source_sha256.
```

## Chain acquisition, research, and export

```text
Work in three explicit stages. First, use youtube-acquire to preview URL and
wait for my confirmation if the source contains multiple videos. After the
confirmed acquisition, use youtube-research to answer QUESTION with timestamped
passages. Export only the videos I select afterward with youtube-export.
```

The final recipe preserves the write boundaries. The researcher remains read-only, while acquisition and export run in the main session.
