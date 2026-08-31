# Fresh-client canaries, 31 August 2026

Global activation remains `false`.

| Client | Status | Evidence boundary |
| --- | --- | --- |
| Codex CLI 0.150.1 | `PASS` | A fresh ephemeral process discovered and opened the project-local cumulative skill, then returned every mandatory human boundary exactly. |
| Claude Code | `UNKNOWN` | The installed CLI reports `loggedIn: false`; no authenticated fresh-session behavior was tested. |

## Codex canary

The passing canary used a fresh process with user configuration and rules
ignored and a read-only sandbox:

```bash
codex exec \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --sandbox read-only \
  '<READ_ONLY_CANARY_PROMPT>'
```

The prompt allowed only the read-only local command needed to open
`.agents/skills/youtube-cumulative-research/SKILL.md`. It prohibited YouTube,
external sources, MCP calls, file writes, and database writes.

The process opened the project-local skill and reported:

- `confirm_sufficiency_or_refresh`;
- `approve_candidates_or_cancel`;
- at most 10 discovery candidates;
- at most 5 approved exact IDs;
- no automatic discovery;
- no automatic acquisition.

An earlier diagnostic prompt returned `SKILL_UNAVAILABLE` because it prohibited
all shell commands, including the only available read-only mechanism for
opening `SKILL.md`. `codex debug prompt-input` confirmed that the cumulative
skill was present in the model-visible catalogue. That first result was an
invalid test design, not a failed discovery gate.

## Remaining boundary

This proves project-local Codex discovery and instruction reading. It does not
prove global installation, Claude Code behavior, live YouTube discovery,
acquisition, relevance, or dossier usefulness. Those gates remain separate.

The public tracking receipt is recorded in
[GitHub issue #14](https://github.com/FlorianBruniaux/youtube-video-insights/issues/14#issuecomment-5481583745).
