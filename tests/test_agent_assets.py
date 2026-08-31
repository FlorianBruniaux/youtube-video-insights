from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "youtube-acquire",
    "youtube-research",
    "youtube-export",
    "youtube-cumulative-research",
)
LEGACY_CLAUDE_SKILLS = {
    "yt-add-channel.md": "youtube-acquire",
    "yt-get-transcript.md": "youtube-acquire",
    "yt-get-insights.md": "youtube-research",
    "yt-get-shorts.md": None,
    "yt-run-pipeline.md": "youtube-acquire",
}
CLAUDE_AGENT = (
    REPOSITORY_ROOT / ".claude" / "agents" / "youtube-corpus-researcher.md"
)
CODEX_AGENT = (
    REPOSITORY_ROOT / ".codex" / "agents" / "youtube-corpus-researcher.toml"
)
PROMPT_EXAMPLES = REPOSITORY_ROOT / "examples" / "agent-prompts.md"


def parse_frontmatter(path: Path) -> tuple[dict[str, str], str]:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(?P<header>.*?)\n---\n(?P<body>.*)", text, re.DOTALL)
    assert match is not None, f"{path} must contain one YAML frontmatter block"
    frontmatter: dict[str, str] = {}
    for line in match.group("header").splitlines():
        if not line or line.startswith((" ", "-")):
            continue
        key, separator, value = line.partition(":")
        assert separator, f"invalid frontmatter line in {path}: {line!r}"
        frontmatter[key] = value.strip().strip('"')
    return frontmatter, match.group("body")


def parse_frontmatter_lists(path: Path) -> dict[str, str | list[str]]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<header>.*?)\n---\n", text, re.DOTALL)
    assert match is not None, f"{path} must contain one YAML frontmatter block"
    parsed: dict[str, str | list[str]] = {}
    active_list: list[str] | None = None
    for line in match.group("header").splitlines():
        if line.startswith("  - "):
            assert active_list is not None, f"orphan list item in {path}"
            active_list.append(line.removeprefix("  - ").strip())
            continue
        key, separator, value = line.partition(":")
        assert separator, f"invalid frontmatter line in {path}: {line!r}"
        if value.strip():
            parsed[key] = value.strip().strip('"')
            active_list = None
        else:
            active_list = []
            parsed[key] = active_list
    return parsed


@pytest.mark.parametrize("name", SKILLS)
def test_agent_skill_has_portable_entrypoint(name: str) -> None:
    root = REPOSITORY_ROOT / ".agents" / "skills" / name
    frontmatter, body = parse_frontmatter(root / "SKILL.md")

    assert frontmatter["name"] == name
    assert 20 <= len(frontmatter["description"]) <= 500
    assert "/Users/" not in body
    assert "yt-dlp " not in body
    assert (root / "agents" / "openai.yaml").is_file()


@pytest.mark.parametrize("name", SKILLS)
def test_agent_skill_rejects_unsafe_shortcuts(name: str) -> None:
    skill = (
        REPOSITORY_ROOT / ".agents" / "skills" / name / "SKILL.md"
    ).read_text(encoding="utf-8")
    lowered = skill.lower()

    assert not re.search(r"echo\s+\$\{?\w*(?:key|token|secret)", lowered)
    assert "cat ~/.claude" not in lowered
    assert "cat ~/.codex" not in lowered
    assert not re.search(r"\b(?:select|insert|update|delete)\b.+\b(?:from|into|set)\b", lowered)
    assert "bypass" not in lowered


def test_acquire_skill_preserves_preview_and_confirmation_boundary() -> None:
    body = parse_frontmatter(
        REPOSITORY_ROOT / ".agents" / "skills" / "youtube-acquire" / "SKILL.md"
    )[1]

    assert "yt-insights doctor --json" in body
    assert "yt-insights acquire SOURCE --dry-run --json" in body
    assert "--yes" in body
    assert "selected" in body
    assert "ready" in body
    assert "failed" in body
    assert "--cookies-from-browser chrome" in body


def test_research_skill_uses_only_the_read_only_mcp_surface() -> None:
    body = parse_frontmatter(
        REPOSITORY_ROOT / ".agents" / "skills" / "youtube-research" / "SKILL.md"
    )[1]

    positions = [
        body.index(tool)
        for tool in ("list_corpora", "search_videos", "search_passages", "get_passage")
    ]
    assert positions == sorted(positions)
    assert "timestamped URL" in body
    assert "bounded excerpt" in body
    assert "yt-insights acquire" not in body
    assert "yt-insights export" not in body


def test_export_skill_uses_portable_mcp_discovery_and_deterministic_export() -> None:
    body = parse_frontmatter(
        REPOSITORY_ROOT / ".agents" / "skills" / "youtube-export" / "SKILL.md"
    )[1]

    assert "yt-insights catalog search" not in body
    assert "read-only MCP" in body
    assert "YouTube URL or video ID" in body
    assert "yt-insights export video" in body
    assert "source_sha256" in body
    assert "--json" in body
    assert "yt-insights acquire" not in body


@pytest.mark.parametrize("name", SKILLS)
def test_openai_metadata_is_implicit_and_has_a_skill_prompt(name: str) -> None:
    metadata = (
        REPOSITORY_ROOT / ".agents" / "skills" / name / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert "display_name:" in metadata
    assert "short_description:" in metadata
    assert f"${name}" in metadata
    assert "allow_implicit_invocation: false" not in metadata


def test_research_metadata_declares_the_local_mcp_dependency() -> None:
    metadata = (
        REPOSITORY_ROOT
        / ".agents"
        / "skills"
        / "youtube-research"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert 'type: "mcp"' in metadata
    assert 'value: "yt-insights"' in metadata


def test_export_metadata_declares_the_local_mcp_dependency() -> None:
    metadata = (
        REPOSITORY_ROOT
        / ".agents"
        / "skills"
        / "youtube-export"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert 'type: "mcp"' in metadata
    assert 'value: "yt-insights"' in metadata


def test_cumulative_research_skill_preserves_human_approval_boundaries() -> None:
    body = parse_frontmatter(
        REPOSITORY_ROOT
        / ".agents"
        / "skills"
        / "youtube-cumulative-research"
        / "SKILL.md"
    )[1]

    for command in (
        "yt-insights research start",
        "yt-insights research status",
        "yt-insights research decide",
        "yt-insights research discover",
        "yt-insights research approve",
        "yt-insights research acquire",
        "yt-insights research retry",
        "yt-insights research export",
    ):
        assert command in body
    for action in (
        "confirm_sufficiency_or_refresh",
        "approve_candidates_or_cancel",
    ):
        assert action in body
    assert "Never approve candidates on the user's behalf" in body
    assert "exact approved video IDs" in body
    assert "latest revision returned by the previous JSON response" in body
    assert "fresh idempotency key for every distinct mutation" in body
    assert "Reuse a key only to replay the exact same request" in body
    assert (
        "yt-insights research cancel SESSION_ID --revision REVISION "
        "--idempotency-key KEY --json"
    ) in body
    assert "dossier, an article draft, a corpus export, both, or nothing else" in body
    assert "relevance gate" in body
    assert "global activation" in body
    assert "read-only MCP" in body


def test_cumulative_research_metadata_does_not_require_the_read_only_mcp() -> None:
    metadata = (
        REPOSITORY_ROOT
        / ".agents"
        / "skills"
        / "youtube-cumulative-research"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert "$youtube-cumulative-research" in metadata
    assert "dependencies:" not in metadata


def test_codex_researcher_is_read_only_and_inherits_the_model() -> None:
    agent = tomllib.loads(CODEX_AGENT.read_text(encoding="utf-8"))

    assert agent["name"] == "youtube_corpus_researcher"
    assert agent["sandbox_mode"] == "read-only"
    assert "model" not in agent
    instructions = agent["developer_instructions"].lower()
    assert "youtube-research" in instructions
    assert "yt-insights mcp" in instructions
    assert "acquire" not in instructions
    assert "yt-insights export" not in instructions
    assert "write exports" in instructions
    assert "modify indexes" in instructions


def test_claude_researcher_preloads_only_research_skill() -> None:
    frontmatter = parse_frontmatter_lists(CLAUDE_AGENT)

    assert frontmatter["name"] == "youtube-corpus-researcher"
    assert frontmatter["model"] == "inherit"
    assert frontmatter["skills"] == ["youtube-research"]
    assert frontmatter["mcpServers"] == ["yt-insights"]
    assert frontmatter["permissionMode"] == "plan"
    assert frontmatter["tools"] == [
        "Read",
        "Grep",
        "Glob",
        "mcp__yt-insights__list_corpora",
        "mcp__yt-insights__search_videos",
        "mcp__yt-insights__search_passages",
        "mcp__yt-insights__get_passage",
    ]


@pytest.mark.parametrize("path", (CLAUDE_AGENT, CODEX_AGENT))
def test_researcher_agent_has_a_source_backed_output_contract(path: Path) -> None:
    content = path.read_text(encoding="utf-8").lower()

    for required in ("claim", "passage", "timestamped url", "coverage limit"):
        assert required in content
    assert "unresolved" in content
    assert "/users/" not in content
    assert "yt-dlp " not in content
    assert "yt-insights acquire" not in content
    assert "yt-insights export" not in content


def test_ready_to_copy_agent_prompts_are_in_english() -> None:
    content = PROMPT_EXAMPLES.read_text(encoding="utf-8")

    for expected in (
        "Use youtube-acquire",
        "Use youtube-research",
        "Use youtube-corpus-researcher",
        "Use youtube-export",
    ):
        assert expected in content
    for forbidden in ("Utilise ", "Nommez ", "Ne télécharge", "Travaille en"):
        assert forbidden not in content


@pytest.mark.parametrize(("filename", "replacement"), LEGACY_CLAUDE_SKILLS.items())
def test_legacy_claude_skill_is_explicit_only(
    filename: str, replacement: str | None
) -> None:
    path = REPOSITORY_ROOT / ".claude" / "skills" / filename
    frontmatter, body = parse_frontmatter(path)

    assert frontmatter["disable-model-invocation"] == "true"
    assert "compatibilité explicite" in body
    if replacement is not None:
        assert replacement in body
