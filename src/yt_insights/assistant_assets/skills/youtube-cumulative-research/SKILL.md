---
name: youtube-cumulative-research
description: Build or resume a source-backed YouTube research session that checks the local yt-insights corpus first, asks before discovery and acquisition, grows the corpus only with explicit approval, reassesses coverage, and optionally exports a dossier. Use for evolving research topics, freshness checks, or research dossiers. Do not use for a simple read-only corpus lookup.
---

# Run cumulative YouTube research

Run this workflow in the main session because discovery and approved acquisition may use the network and write local source files. Use the packaged `yt-insights research` CLI as the source of truth. The read-only MCP may help with a separate local lookup, but never use it for a research state transition or mutation.

## Start or resume

- Start with `yt-insights research start "TOPIC" --query "QUERY" --freshness-profile standard --json`. Use one to eight explicit queries. Do not add hidden query expansion.
- Resume with `yt-insights research status SESSION_ID --json`. Preserve the returned session ID and revision exactly.
- Treat invalid JSON or an unknown schema as unverified state. Stop, show a bounded error, and do not infer a command, revision, candidate, or result.

Present the recorded queries, freshness profile, coverage counts, newest relevant source date, last successful discovery date, unknown dates, and explicit coverage limits. Keep claims tied to stored evidence, source hashes, and timestamped URLs.

## Required human decisions

Interpret `required_user_action` literally:

- For `confirm_sufficiency_or_refresh`, ask whether the current evidence is sufficient or whether the user wants newer YouTube candidates. Record the answer with `yt-insights research decide SESSION_ID sufficient|refresh --revision REVISION --idempotency-key KEY --json`.
- A `refresh` decision authorizes discovery only. Run `yt-insights research discover SESSION_ID --revision REVISION --json`, then use `yt-insights research candidates SESSION_ID --json` to present at most ten candidates with ID, title, channel, publication date, matched query, and URL.
- For `approve_candidates_or_cancel`, ask the user to select one to five exact candidate IDs or cancel. Never approve candidates on the user's behalf. Repeat only the selected IDs, unchanged, in `yt-insights research approve SESSION_ID VIDEO_ID... --revision REVISION --idempotency-key KEY --json`. Use `yt-insights research cancel` only when the user chooses to cancel.

After approval, acquire only the exact approved video IDs with `yt-insights research acquire SESSION_ID --revision REVISION --idempotency-key KEY --json`. Do not substitute a missing video, expand to a playlist or channel, or add related videos. Present per-video outcomes, the refreshed assessment, and the sufficiency question again.

Use `yt-insights research retry SESSION_ID --revision REVISION --idempotency-key KEY --json` only for the retryable transition reported by the session. Never replay a committed decision or a successful acquisition.

## Output choice

After sufficiency is confirmed, ask whether the user wants a dossier, an article draft, a corpus export, both, or nothing else.

- Export deterministic evidence with `yt-insights research export SESSION_ID --output ABSOLUTE_DIRECTORY --json`.
- Draft prose only after that explicit choice. Keep model synthesis separate from YouTube source evidence and state unresolved questions and coverage limits.
- A transcript corpus export uses the existing exact-video export workflow and an explicit destination. Generated dossiers and prose never become source passages.

## Fail closed

- Missing session: stop and ask for a valid session ID or permission to start a new session.
- Stale revision: run `yt-insights research status SESSION_ID --json`, present the changed state, and ask for the needed decision again. Do not replay the rejected command.
- Unavailable network or provider: preserve the session, report the bounded failure, and stop after the failed call. Retry only when the user requests it.
- If the relevance gate is `FAIL` or `UNKNOWN`, report that retrieval quality and global activation are not validated. The exact local workflow remains available, but do not claim the skill is globally activated or that evidence quality passed its gate.
- Candidate or acquisition failure: preserve recorded outcomes and never choose a replacement automatically.

Do not install or modify global Claude Code or Codex configuration through this skill. Global activation requires a separately approved candidate and fresh-session verification.
