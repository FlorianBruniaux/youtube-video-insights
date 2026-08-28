#!/bin/bash
# UserPromptSubmit hook: routes "analyze a YouTube channel" requests to the
# right tooling before Claude starts improvising.
#
# Why this exists: a channel run without --output-dir writes into the shared
# output/transcripts/ and output/insights/ dirs instead of output/<slug>/.
# That is exactly how ~800 Bloomberg videos ended up mixed into the shared
# folders undetected for weeks. This hook fires only on channel-level intent
# (a channel URL, or channel language without a URL) so it stays silent for
# single-video requests, which use a different skill.
#
# Non-blocking: emits additionalContext, never blocks the prompt.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null || true)
[[ -z "$PROMPT" ]] && exit 0

PROMPT_LC=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]')

# Channel-style YouTube URL: /@handle, /channel/UC..., /c/name, /user/name
# (with or without a trailing /videos). Deliberately excludes bare
# youtube.com/watch?v=... and youtu.be/... single-video links.
HAS_CHANNEL_URL=0
if echo "$PROMPT_LC" | grep -qE 'youtube\.com/(@|channel/|c/|user/)'; then
    HAS_CHANNEL_URL=1
fi

# Natural-language channel intent, even without a URL pasted in this prompt.
# Accents matched with "." wildcards (é/è/ê) instead of enumerated character
# classes: a French keyboard mixing acute/grave accents silently breaks a
# [ée]-style class and the hook goes quiet without any error.
HAS_CHANNEL_INTENT=0
if echo "$PROMPT_LC" | grep -qE '(cette cha.ne|la cha.ne|this channel|toute la cha.ne|toutes les vid.os|whole channel|entire channel)'; then
    HAS_CHANNEL_INTENT=1
fi

[[ "$HAS_CHANNEL_URL" -eq 0 && "$HAS_CHANNEL_INTENT" -eq 0 ]] && exit 0

# Require an analysis-ish verb so we don't fire on "j'ai regardé cette chaîne hier".
if ! echo "$PROMPT_LC" | grep -qE '(parse|analys|r.cup.r|t.l.charg|indexe|process|ajout|add.*channel|download|extract)'; then
    exit 0
fi

cat << 'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "[yt-insights routing] This looks like a channel-level request, not a single video. Use the /yt-add-channel skill and runbook/run-channel.sh — never call `yt-insights run` on a channel URL without an explicit --output-dir output/<slug>/. Omitting it writes into the shared output/transcripts/ and output/insights/ dirs, which is how ~800 orphan Bloomberg videos ended up unrouted before. After the run, refresh output/CATALOG.yaml and output/INDEX.md via scripts/build_index.py (run-channel.sh already does this), then run `yt-insights catalog import-corpus output` to refresh the SQLite catalog. See runbook/README.md for the cookie, truncation, and language-dedup pitfalls."
  }
}
EOF
