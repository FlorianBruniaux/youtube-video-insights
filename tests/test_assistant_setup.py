from __future__ import annotations

import json
import os
from pathlib import Path

from click.testing import CliRunner

from yt_insights import assistant_setup
from yt_insights.cli import cli


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ASSET_PAIRS = {
    ".agents/skills/youtube-acquire/SKILL.md": "skills/youtube-acquire/SKILL.md",
    ".agents/skills/youtube-acquire/agents/openai.yaml": (
        "skills/youtube-acquire/agents/openai.yaml"
    ),
    ".agents/skills/youtube-research/SKILL.md": "skills/youtube-research/SKILL.md",
    ".agents/skills/youtube-research/agents/openai.yaml": (
        "skills/youtube-research/agents/openai.yaml"
    ),
    ".agents/skills/youtube-export/SKILL.md": "skills/youtube-export/SKILL.md",
    ".agents/skills/youtube-export/agents/openai.yaml": "skills/youtube-export/agents/openai.yaml",
    ".claude/agents/youtube-corpus-researcher.md": "claude/youtube-corpus-researcher.md",
    ".codex/agents/youtube-corpus-researcher.toml": "codex/youtube-corpus-researcher.toml",
}


def _write_fake_client(bin_dir: Path, name: str) -> None:
    path = bin_dir / name
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

client = Path(sys.argv[0]).name
state_root = Path(os.environ["FAKE_CLIENT_STATE"])
state_root.mkdir(parents=True, exist_ok=True)
state = state_root / f"{client}.json"
log = state_root / "operations.jsonl"
args = sys.argv[1:]

with log.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"client": client, "args": args}) + "\\n")

if args[:2] == ["mcp", "get"]:
    if not state.exists():
        raise SystemExit(1)
    print(state.read_text(encoding="utf-8"))
    raise SystemExit(0)

if args[:2] == ["mcp", "add"]:
    if os.environ.get("FAKE_FAIL_CLIENT") == client:
        print("synthetic add failure", file=sys.stderr)
        raise SystemExit(7)
    state.write_text(json.dumps({"name": "yt-insights"}), encoding="utf-8")
    raise SystemExit(0)

if args[:2] == ["mcp", "remove"]:
    state.unlink(missing_ok=True)
    raise SystemExit(0)

raise SystemExit(2)
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def _environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path]:
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    state = tmp_path / "state"
    corpus = tmp_path / "corpus"
    bin_dir.mkdir()
    corpus.mkdir()
    _write_fake_client(bin_dir, "claude")
    _write_fake_client(bin_dir, "codex")
    server = bin_dir / "yt-insights-mcp"
    server.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    server.chmod(0o755)
    env = {
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_CLIENT_STATE": str(state),
    }
    return env, home, state, corpus


def _arguments(corpus: Path, server: Path, mode: str | None) -> list[str]:
    arguments = [
        "setup",
        "assistants",
        "--client",
        "both",
        "--data-root",
        str(corpus),
        "--mcp-command",
        str(server),
        "--json",
    ]
    if mode is not None:
        arguments.insert(-1, mode)
    return arguments


def test_root_cli_registers_setup_command() -> None:
    assert "setup" in cli.commands


def test_packaged_assets_match_the_versioned_sources() -> None:
    packaged = REPOSITORY_ROOT / "src" / "yt_insights" / "assistant_assets"

    for source, relative_asset in ASSET_PAIRS.items():
        assert (packaged / relative_asset).read_bytes() == (
            REPOSITORY_ROOT / source
        ).read_bytes()


def test_dry_run_is_default_and_performs_no_writes(tmp_path: Path) -> None:
    env, home, state, corpus = _environment(tmp_path)
    server = tmp_path / "bin" / "yt-insights-mcp"

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, server, None),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "dry-run"
    assert payload["status"] == "planned"
    assert len(payload["operations"]) == 10
    assert not home.exists()
    assert not state.exists()


def test_conflict_blocks_all_writes_and_client_mutations(tmp_path: Path) -> None:
    env, home, state, corpus = _environment(tmp_path)
    conflict = home / ".agents" / "skills" / "youtube-acquire" / "SKILL.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("local customization\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["conflicts"] == [str(conflict)]
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert not state.exists()


def test_symlinked_parent_is_rejected_without_following_it(tmp_path: Path) -> None:
    env, home, state, corpus = _environment(tmp_path)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    home.mkdir()
    (home / ".agents").symlink_to(redirected, target_is_directory=True)

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert str(home / ".agents" / "skills" / "youtube-acquire" / "SKILL.md") in payload[
        "conflicts"
    ]
    assert not tuple(redirected.rglob("*"))
    assert not state.exists()


def test_apply_installs_assets_and_registers_both_clients(tmp_path: Path) -> None:
    env, home, state, corpus = _environment(tmp_path)

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "installed"
    assert (home / ".agents" / "skills" / "youtube-research" / "SKILL.md").is_file()
    assert (home / ".claude" / "agents" / "youtube-corpus-researcher.md").is_file()
    assert (home / ".codex" / "agents" / "youtube-corpus-researcher.toml").is_file()
    assert (state / "claude.json").is_file()
    assert (state / "codex.json").is_file()


def test_concurrent_target_creation_is_preserved(tmp_path: Path, monkeypatch) -> None:
    env, home, _state, corpus = _environment(tmp_path)
    target = home / ".agents" / "skills" / "youtube-acquire" / "SKILL.md"
    original_write = assistant_setup._write_asset
    raced = False

    def create_target_before_publish(asset: assistant_setup.Asset) -> None:
        nonlocal raced
        if not raced:
            raced = True
            asset.target.parent.mkdir(parents=True)
            asset.target.write_text("concurrent customization\n", encoding="utf-8")
        original_write(asset)

    monkeypatch.setattr(assistant_setup, "_write_asset", create_target_before_publish)

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "concurrent customization\n"


def test_failed_second_registration_rolls_back_new_state(tmp_path: Path) -> None:
    env, home, state, corpus = _environment(tmp_path)
    env["FAKE_FAIL_CLIENT"] = "codex"

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "rolled_back"
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert not (home / ".codex").exists()
    assert not (state / "claude.json").exists()
    operations = [
        json.loads(line)
        for line in (state / "operations.jsonl").read_text().splitlines()
    ]
    assert [(item["client"], item["args"][:2]) for item in operations] == [
        ("claude", ["mcp", "get"]),
        ("codex", ["mcp", "get"]),
        ("claude", ["mcp", "add"]),
        ("codex", ["mcp", "add"]),
        ("codex", ["mcp", "remove"]),
        ("claude", ["mcp", "remove"]),
    ]


def test_rollback_preserves_a_concurrently_replaced_file(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, corpus = _environment(tmp_path)
    env["FAKE_FAIL_CLIENT"] = "codex"
    target = home / ".agents" / "skills" / "youtube-acquire" / "SKILL.md"
    original_remove = assistant_setup._remove_registration
    replaced = False

    def replace_before_file_cleanup(client, executable) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            target.unlink()
            target.write_text("concurrent replacement\n", encoding="utf-8")
        original_remove(client, executable)

    monkeypatch.setattr(
        assistant_setup, "_remove_registration", replace_before_file_cleanup
    )

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "concurrent replacement\n"


def test_verify_detects_complete_and_changed_setup(tmp_path: Path) -> None:
    env, home, _state, corpus = _environment(tmp_path)
    server = tmp_path / "bin" / "yt-insights-mcp"
    runner = CliRunner()

    applied = runner.invoke(
        cli, _arguments(corpus, server, "--apply"), env=env, catch_exceptions=False
    )
    assert applied.exit_code == 0, applied.output

    verified = runner.invoke(
        cli, _arguments(corpus, server, "--verify"), env=env, catch_exceptions=False
    )
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["status"] == "verified"

    target = home / ".agents" / "skills" / "youtube-research" / "SKILL.md"
    target.write_text("changed\n", encoding="utf-8")
    changed = runner.invoke(cli, _arguments(corpus, server, "--verify"), env=env)
    assert changed.exit_code != 0
    assert json.loads(changed.output)["status"] == "invalid"


def test_relative_data_root_is_rejected_without_side_effects(tmp_path: Path) -> None:
    env, home, state, _corpus = _environment(tmp_path)
    args = _arguments(Path("relative-corpus"), tmp_path / "bin" / "yt-insights-mcp", "--apply")

    result = CliRunner().invoke(cli, args, env=env)

    assert result.exit_code != 0
    assert "absolute" in result.output.lower()
    assert not home.exists()
    assert not state.exists()


def test_json_output_never_echoes_provider_secrets(tmp_path: Path) -> None:
    env, _home, _state, corpus = _environment(tmp_path)
    env["ANTHROPIC_API_KEY"] = "should-not-appear"
    env["OPENAI_API_KEY"] = "also-secret"

    result = CliRunner().invoke(
        cli,
        _arguments(corpus, tmp_path / "bin" / "yt-insights-mcp", "--dry-run"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    assert "should-not-appear" not in result.output
    assert "also-secret" not in result.output
