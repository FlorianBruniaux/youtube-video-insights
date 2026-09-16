from pathlib import Path

from scripts.build_speakers import scan_channel


def test_indydevdan_tool_phrases_are_not_speakers(tmp_path: Path) -> None:
    titles = (
        "20250526 - Claude Code with Git Worktrees [f8RnRuaxee8].en-orig.json",
        "20250217 - AI Coding with Aider Architect [YAIJV48QlXc].en-orig.json",
        "20240722 - Prompt Chain with SOTA Accuracy [0Z2BQPuUY50].en-orig.json",
    )
    for title in titles:
        (tmp_path / title).touch()

    assert scan_channel(tmp_path, "indydevdan") == set()


def test_named_guest_is_still_detected(tmp_path: Path) -> None:
    (tmp_path / "20250101 - Agent Design with Jane Doe [abcdefghijk].en.json").touch()

    assert scan_channel(tmp_path, "aidevcon") == {"Jane Doe"}
