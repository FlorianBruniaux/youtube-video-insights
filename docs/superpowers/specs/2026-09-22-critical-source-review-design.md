# Critical source review for YouTube research

**Status:** Accepted product requirement; runtime not implemented.
**Design revision:** 1, 2026-09-22.
**Scope:** An explicit, advisory review stage for local research and editorial selection.

## Purpose and current boundary

Help a reader decide whether a video contributes useful evidence or explanation
to a stated research question. A recent video, matching transcript, popular
channel, or high Shorts score does not establish source quality.

The shipped research assessment measures coverage and freshness. The insights
pipeline produces summaries, and the Shorts pipeline scores clips. None of
these is a validated critical review of evidential or editorial value. This
design does not change the current runtime, assistant assets, or confirmations.

## Place in the workflow

| Stage | Input | Allowed output |
| --- | --- | --- |
| Candidate screening | The exact metadata snapshot returned by discovery | Apparent topic fit and questions to investigate before acquisition |
| Critical reading | Acquired VTT, stable timestamped passages, and explicit comparison sources | Evidence-backed findings limited to the inspected transcript coverage |

Candidate screening cannot inspect a transcript that has not been acquired.
It must not infer low quality from a title or channel name, or acquire more
sources implicitly. The separate refresh and exact-ID acquisition approvals
remain mandatory, including the existing limits of ten candidates and one to
five approved IDs.

Critical reading belongs after local acquisition/indexing and before choosing
sources for an editorial dossier. It is an explicit operation. Ordinary search,
coverage assessment, deterministic export, and read-only MCP requests remain
free of implicit LLM calls and network discovery.

## Video-specific rubric

Keep dimensions separate, each with reasons, timestamped evidence, uncertainty,
and missing checks. Evidential support is `supported`, `mixed`, or `UNKNOWN`;
there is no universal score that ranks videos against scientific papers.

| Dimension | Review question | Evidence boundary |
| --- | --- | --- |
| Relevance | Does it address the research question and intended audience? | Topic match is not proof of factual accuracy |
| Added value | Does it add an explanation, result, counterexample, or useful detail? | Compare only against explicitly identified sources |
| Evidence | Are claims supported by described experiments, examples, or cited sources? | A spoken assertion is evidence that it was said, not that it is true |
| Precision | Are conditions, limitations, and failures made explicit? | Missing context remains visible rather than reconstructed |
| Repetition | Does it substantially duplicate already selected material? | Record matching passages and comparator identities; do not infer intent |
| Usability | Is the contribution useful for the selected audience and purpose? | Tutorials and replications may be useful without being novel |

Record observed promotional framing or sponsorship only when sourced. Neither
commercial content, popularity, speaker reputation, transcript length, nor
production style is sufficient for a negative verdict. Promotional claims
with no inspected support should be identified at claim level.

## Coverage and evidence limits

Reviews bind `video_id`, VTT SHA-256, inspected passage IDs and timestamp ranges,
language, and source/index identity. Expose inspected versus available passage
counts, gaps, truncation, and the selected sampling or chunking policy.

The current analysis input cap is configurable and defaults to 10,000 transcript
characters. It must not be reused as an implicit whole-video review. A bounded
excerpt review is labelled partial and cannot justify a whole-video quality
verdict when uninspected passages could change the conclusion.

VTT cannot verify an unseen slide, visual demonstration, or linked dataset.
Record these as uninspected evidence. Citation of an external source does not
mean that source was checked; any additional retrieval requires its own explicit
authorization and source record. No audio or visual acquisition is introduced
by this design.

## Recommendations, decisions, and retention

The proposed recommendation vocabulary is `retain`, `defer`,
`exclude_from_selection`, and `insufficient_evidence`. These are design terms,
not new CLI commands or values accepted by the shipped API.

- Retain with an explanation of the useful contribution and its limits.
- Defer when a named missing check can change the decision.
- Exclude from this selection with sourced reasons, never from the raw corpus.
- Abstain as `insufficient_evidence` when the available material cannot support
  the decision; failure or missing subtitles is not a low-quality verdict.

The first release is advisory. Keep the model recommendation separate from the
human decision, including acceptance, override, and later reversal. Unreviewed
sources remain visible, and an exclusion is scoped to a topic, audience, and
selection revision. No source file, FTS passage, search result, or acquisition
approval is deleted or silently changed by a recommendation.

Reviewed dossiers must expose reviewed, unreviewed, retained, deferred, and
excluded sources, with reasons and evidence links. A review does not answer
the existing sufficiency question on the user's behalf. Ordinary export stays
backward compatible; a future reviewed export uses an explicitly versioned
manifest and a recorded selection policy.

## Durable records and invalidation

The research store is the owner of future review history, separate from raw
VTT and the replaceable catalogue/FTS projections. Add tables only through a
reviewed migration. Model contract fields must include:

- source identity and SHA-256, exact evidence scope and coverage;
- topic, intended audience, rubric version, and frozen comparison set;
- findings, evidence locators, uncertainties, counterevidence, recommendation,
  reasons, and validation state;
- prompt version/hash, provider/model, generation parameters, timestamp, stop
  reason, and bounded execution limits;
- human decision events with actor, revision, time, reason, and any superseded
  decision. Agents never fabricate human approvals.

Use revision checks and idempotency for review publication and selection
mutations. A changed transcript, context, comparator, rubric, prompt, or model
invalidates current applicability; retain previous reviews as history.
Malformed, stale, truncated, or failed output never replaces a valid review.

Validate every passage against the recorded video and VTT hash before publishing
a completed review. Rebuilding FTS must not detach evidence from its immutable
source. Review records and dossier prose never enter source indexes. Treat
transcript content as untrusted data, including requests to alter the review.

## Implementation order and acceptance

1. Freeze closed review/selection schemas and a versioned rubric. Declare
   maximum sources, inspected text, model calls, retries, and total execution
   budget for each explicit run; record any incomplete coverage.
2. Implement research-store migrations and event history, with rollback,
   stale-revision, replay, source-change, and override tests.
3. Add a review service over immutable local evidence with an injected backend.
   Use local VTT fixtures and mocked responses to test support resolution,
   partial coverage, prompt injection, invalid output, and abstention.
4. Integrate an explicit CLI operation and versioned reviewed dossier, then
   expose the same service through the local API and web interface. Update
   assistant assets only when their referenced commands exist. Keep MCP
   read-only and preserve both acquisition confirmations.
5. Calibrate on a frozen human-reviewed set before claiming quality or enabling
   automated selection. Record denominators, disagreements, abstentions, false
   exclusions of useful videos, and results by content type. Agree thresholds
   before evaluating the held-out set. Prefer short evidence cards for review.

Relevant implementation areas are `src/yt_insights/research/models.py`,
`research/store.py`, a new review service under `research/`,
`cli_research.py`, and `research/dossier.py`. API and web work follow the CLI
contract. Existing `assessment.py` remains deterministic and read-only.

Required acceptance cases include a useful introductory tutorial, a repeated
talk with one new result, a claim whose supporting experiment is outside the
sample, an unavailable visual demonstration, unsupported promotional claims,
a changed transcript, and an explicit override of a proposed exclusion.
Each case must preserve source availability and ordinary search behavior.

The existing 20-result retrieval relevance gate remains independent. Its
acceptance, a successful summary, or passing structural tests does not validate
this rubric. Until calibration is complete, review quality is `UNKNOWN` and
unattended filtering remains disabled.

## Companion contract

[Paper Insights](https://github.com/FlorianBruniaux/paper-insights/blob/main/docs/specs/CRITICAL-REVIEW.md)
shares the evidence, abstention, and reversible-selection principles. Its
rubric adds scientific methods and bibliographic versioning. A video quoting
a paper is not an independent corroborating source. Cross-corpus federation
remains planned, and no shared database or score is introduced here.
