# Claude Code and Codex prompts

Name the skill or agent explicitly in each prompt. YT Insights keeps routing explicit so that a general YouTube request cannot trigger local acquisition or corpus research by mistake.

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
