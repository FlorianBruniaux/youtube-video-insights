from __future__ import annotations

import re
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("youtube-acquire", "youtube-research", "youtube-export")


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


def test_export_skill_uses_catalog_discovery_and_deterministic_export() -> None:
    body = parse_frontmatter(
        REPOSITORY_ROOT / ".agents" / "skills" / "youtube-export" / "SKILL.md"
    )[1]

    assert "catalog search" in body
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
