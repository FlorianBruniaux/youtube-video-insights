from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = REPOSITORY_ROOT / "examples" / "agent-prompts.md"
CUMULATIVE_HEADINGS = (
    "Research AI workflows in product and engineering teams",
    "Research cost-efficient local inference with MLX and Ollama",
    "Research AI-assisted code quality",
    "Resume a cumulative research session",
    "Refresh stale evidence and review candidates",
    "Approve exact candidate IDs",
    "Export a deterministic research dossier",
    "Copy a dossier into the current project",
)


def _prompt_after_heading(document: str, heading: str) -> str:
    pattern = rf"^## {re.escape(heading)}\n\n```text\n(.*?)\n```"
    match = re.search(pattern, document, flags=re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing copy-ready prompt: {heading}"
    return match.group(1)


def test_cumulative_examples_preserve_evidence_and_coverage_requests() -> None:
    document = PROMPTS.read_text(encoding="utf-8")

    for heading in CUMULATIVE_HEADINGS:
        prompt = " ".join(_prompt_after_heading(document, heading).split())
        assert "youtube-cumulative-research" in prompt
        assert "timestamp" in prompt.casefold()
        assert "coverage limits" in prompt.casefold()


def test_cumulative_examples_cover_the_stable_mutation_and_export_contract() -> None:
    document = PROMPTS.read_text(encoding="utf-8")
    cumulative_prompts = " ".join(
        _prompt_after_heading(document, heading) for heading in CUMULATIVE_HEADINGS
    )
    cumulative_prompts = " ".join(cumulative_prompts.split())

    for command in (
        "research start",
        "research status",
        "research decide",
        "research discover",
        "research candidates",
        "research approve",
        "research acquire",
        "research export",
    ):
        assert command in cumulative_prompts
    assert "SESSION_ID" in cumulative_prompts
    assert "VIDEO_ID_A VIDEO_ID_B" in cumulative_prompts
    assert "$PWD" in cumulative_prompts


def test_refresh_prompt_uses_the_revision_returned_by_the_decision() -> None:
    document = PROMPTS.read_text(encoding="utf-8")
    prompt = " ".join(
        _prompt_after_heading(
            document, "Refresh stale evidence and review candidates"
        ).split()
    )

    assert (
        "research decide SESSION_ID refresh --revision CURRENT_REVISION "
        "--idempotency-key KEY --json"
    ) in prompt
    assert "Use DECISION_REVISION returned by that decide response" in prompt
    assert (
        "research discover SESSION_ID --revision DECISION_REVISION --json"
    ) in prompt
    assert "research discover SESSION_ID --revision CURRENT_REVISION" not in prompt
