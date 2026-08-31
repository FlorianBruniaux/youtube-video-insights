from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from click.testing import CliRunner

from yt_insights import assistant_setup
from yt_insights.cli import cli


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OWNERSHIP_MANIFEST = Path(".agents/.yt-insights-assistant-assets-v1.json")
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
    ".agents/skills/youtube-cumulative-research/SKILL.md": (
        "skills/youtube-cumulative-research/SKILL.md"
    ),
    ".agents/skills/youtube-cumulative-research/agents/openai.yaml": (
        "skills/youtube-cumulative-research/agents/openai.yaml"
    ),
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


def _assets_only_arguments(mode: str) -> list[str]:
    return ["setup", "assistants", "--client", "both", "--assets-only", mode, "--json"]


def _changed_asset_root(tmp_path: Path, relative: str, content: str) -> Path:
    root = tmp_path / "changed-assets"
    shutil.copytree(
        REPOSITORY_ROOT / "src" / "yt_insights" / "assistant_assets",
        root,
    )
    (root / relative).write_text(content, encoding="utf-8")
    return root


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
    assert len(payload["operations"]) == 12
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
    assert (
        home / ".agents" / "skills" / "youtube-cumulative-research" / "SKILL.md"
    ).is_file()
    assert (home / ".claude" / "agents" / "youtube-corpus-researcher.md").is_file()
    assert (home / ".codex" / "agents" / "youtube-corpus-researcher.toml").is_file()
    assert (state / "claude.json").is_file()
    assert (state / "codex.json").is_file()


def test_concurrent_target_creation_is_preserved(tmp_path: Path, monkeypatch) -> None:
    env, home, _state, corpus = _environment(tmp_path)
    target = home / ".agents" / "skills" / "youtube-acquire" / "SKILL.md"
    original_write = assistant_setup._write_asset
    raced = False

    def create_target_before_publish(
        asset: assistant_setup.Asset,
        root: assistant_setup.BoundRoot,
    ) -> assistant_setup.CreatedFile:
        nonlocal raced
        if not raced:
            raced = True
            asset.target.parent.mkdir(parents=True, exist_ok=True)
            asset.target.write_text("concurrent customization\n", encoding="utf-8")
        return original_write(asset, root)

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


def test_assets_only_apply_needs_no_data_root_or_executable_and_ignores_mcp_state(
    tmp_path: Path,
) -> None:
    env, home, state, _corpus = _environment(tmp_path)
    state.mkdir()
    claude_state = state / "claude.json"
    claude_state.write_text('{"custom": true}\n', encoding="utf-8")
    before = claude_state.read_bytes()

    result = CliRunner().invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "installed"
    assert payload["assets_only"] is True
    assert len(payload["operations"]) == 10
    assert {operation["kind"] for operation in payload["operations"]} == {"copy"}
    assert "data_root" not in payload
    assert "mcp_command" not in payload
    assert "registered_clients" not in payload
    assert claude_state.read_bytes() == before
    assert not (state / "operations.jsonl").exists()
    assert (
        home / ".agents" / "skills" / "youtube-cumulative-research" / "SKILL.md"
    ).is_file()
    manifest = json.loads((home / OWNERSHIP_MANIFEST).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert set(manifest) == {"schema_version", "assets"}
    assert set(manifest["assets"]) == set(ASSET_PAIRS)
    assert all(
        len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        for digest in manifest["assets"].values()
    )


def test_assets_only_dry_run_and_apply_upgrade_a_managed_asset(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    old_bytes = target.read_bytes()
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()
    changed_root = _changed_asset_root(tmp_path, relative, "managed upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)

    preview = runner.invoke(
        cli,
        _assets_only_arguments("--dry-run"),
        env=env,
        catch_exceptions=False,
    )
    assert preview.exit_code == 0, preview.output
    preview_payload = json.loads(preview.output)
    operation = next(
        item for item in preview_payload["operations"] if item["target"] == str(target)
    )
    assert operation["status"] == "upgrade"
    assert target.read_bytes() == old_bytes
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before

    upgraded = runner.invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )
    assert upgraded.exit_code == 0, upgraded.output
    assert json.loads(upgraded.output)["upgraded_files"] == 1
    assert target.read_text(encoding="utf-8") == "managed upgrade\n"
    new_manifest = json.loads((home / OWNERSHIP_MANIFEST).read_text(encoding="utf-8"))
    assert new_manifest["assets"][f".agents/{relative}"] != json.loads(
        manifest_before
    )["assets"][f".agents/{relative}"]


def test_assets_only_missing_manifest_cannot_authorize_an_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output
    (home / OWNERSHIP_MANIFEST).unlink()

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    before = target.read_bytes()
    changed_root = _changed_asset_root(tmp_path, relative, "untrusted upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["conflicts"] == [str(target)]
    assert target.read_bytes() == before
    assert not (home / OWNERSHIP_MANIFEST).exists()


def test_assets_only_corrupt_manifest_blocks_all_writes(tmp_path: Path) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output
    manifest = home / OWNERSHIP_MANIFEST
    manifest.write_bytes(b"not-json\n")
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["conflicts"] == [str(manifest)]
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}


def test_assets_only_customized_managed_asset_is_not_upgraded(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    target.write_text("user customization\n", encoding="utf-8")
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["conflicts"] == [str(target)]
    assert target.read_text(encoding="utf-8") == "user customization\n"
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before


def test_assets_only_concurrent_change_aborts_before_upgrade(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()
    original_snapshot = assistant_setup._snapshot_file
    changed = False

    def change_after_inspection(path: Path):
        nonlocal changed
        result = original_snapshot(path)
        if path == target and not changed:
            changed = True
            target.write_text("concurrent customization\n", encoding="utf-8")
        return result

    monkeypatch.setattr(assistant_setup, "_snapshot_file", change_after_inspection)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "concurrent customization\n"
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before
    assert not tuple(target.parent.glob(f".{target.name}.backup.*"))


def test_assets_only_concurrent_change_to_identical_asset_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    target = home / ".agents" / "skills" / "youtube-research" / "SKILL.md"
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()
    original_snapshot = assistant_setup._snapshot_file
    changed = False

    def change_after_inspection(path: Path):
        nonlocal changed
        result = original_snapshot(path)
        if path == target and not changed:
            changed = True
            target.write_text("concurrent customization\n", encoding="utf-8")
        return result

    monkeypatch.setattr(assistant_setup, "_snapshot_file", change_after_inspection)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "concurrent customization\n"
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before


def test_assets_only_concurrent_manifest_change_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    manifest = home / OWNERSHIP_MANIFEST
    original_snapshot = assistant_setup._snapshot_file
    changed = False

    def change_after_inspection(path: Path):
        nonlocal changed
        result = original_snapshot(path)
        if path == manifest and not changed:
            changed = True
            manifest.write_text('{"concurrent": true}\n', encoding="utf-8")
        return result

    monkeypatch.setattr(assistant_setup, "_snapshot_file", change_after_inspection)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert manifest.read_text(encoding="utf-8") == '{"concurrent": true}\n'


def test_assets_only_mid_upgrade_failure_restores_assets_and_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    changed_root = tmp_path / "changed-assets"
    shutil.copytree(
        REPOSITORY_ROOT / "src" / "yt_insights" / "assistant_assets",
        changed_root,
    )
    for relative, content in (
        ("skills/youtube-acquire/SKILL.md", "first upgrade\n"),
        ("skills/youtube-acquire/agents/openai.yaml", "second upgrade\n"),
    ):
        (changed_root / relative).write_text(content, encoding="utf-8")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    original_replace = assistant_setup._replace_managed_file
    calls = 0

    def fail_second_replacement(
        target: Path,
        content: bytes,
        expected: assistant_setup.FileSnapshot,
        root: assistant_setup.BoundRoot,
    ) -> assistant_setup.ReplacedFile:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic second upgrade failure")
        return original_replace(target, content, expected, root)

    monkeypatch.setattr(
        assistant_setup,
        "_replace_managed_file",
        fail_second_replacement,
    )

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert not tuple(home.rglob("*.backup.*"))


def test_assets_only_publication_failure_restores_preimage_without_backup_leak(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    original_link = assistant_setup.os.link

    def fail_new_publication(
        source: Path | str,
        destination: Path | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        source_path = Path(source)
        if Path(destination) == Path(target.name) and f".{target.name}.new." in source_path.name:
            raise OSError("synthetic publication failure")
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(assistant_setup.os, "link", fail_new_publication)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert not tuple(home.rglob("*.backup.*"))


def test_assets_only_substitution_between_link_and_snapshot_preserves_concurrent_file(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()
    original_link = assistant_setup.os.link
    substituted = False

    def substitute_after_link(
        source: Path | str,
        destination: Path | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal substituted
        original_link(source, destination, *args, **kwargs)
        if (
            not substituted
            and Path(destination) == Path(target.name)
            and f".{target.name}.new." in Path(source).name
        ):
            substituted = True
            parent_fd = int(kwargs["dst_dir_fd"])
            os.unlink(target.name, dir_fd=parent_fd)
            descriptor = os.open(
                target.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o644,
                dir_fd=parent_fd,
            )
            try:
                os.write(descriptor, b"concurrent replacement\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    monkeypatch.setattr(assistant_setup.os, "link", substitute_after_link)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert target.read_text(encoding="utf-8") == "concurrent replacement\n"
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before
    assert not tuple(target.parent.glob(f".{target.name}.new.*"))
    assert not tuple(target.parent.glob(f".{target.name}.backup.*"))


def test_assets_only_snapshot_failure_after_link_restores_exact_preimage(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    before = {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    original_link = assistant_setup.os.link
    original_snapshot = assistant_setup._snapshot_at
    publication_linked = False

    def mark_publication(
        source: Path | str,
        destination: Path | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal publication_linked
        original_link(source, destination, *args, **kwargs)
        if (
            Path(destination) == Path(target.name)
            and f".{target.name}.new." in Path(source).name
        ):
            publication_linked = True

    def fail_target_snapshot(parent_fd: int, name: str):
        if publication_linked and name == target.name:
            raise OSError("synthetic post-link snapshot failure")
        return original_snapshot(parent_fd, name)

    monkeypatch.setattr(assistant_setup.os, "link", mark_publication)
    monkeypatch.setattr(assistant_setup, "_snapshot_at", fail_target_snapshot)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert before == {path: path.read_bytes() for path in home.rglob("*") if path.is_file()}
    assert not tuple(target.parent.glob(f".{target.name}.new.*"))
    assert not tuple(target.parent.glob(f".{target.name}.backup.*"))


def test_assets_only_parent_symlink_swap_never_redirects_publication(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    relative = "skills/youtube-cumulative-research/SKILL.md"
    target = home / ".agents" / relative
    original_bytes = target.read_bytes()
    changed_root = _changed_asset_root(tmp_path, relative, "packaged upgrade\n")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    moved_parent = target.parent.with_name(f"{target.parent.name}.moved")
    redirected = tmp_path / "redirected-parent"
    redirected.mkdir()
    original_link = assistant_setup.os.link
    swapped = False

    def swap_parent_before_link(
        source: Path | str,
        destination: Path | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal swapped
        if (
            not swapped
            and Path(destination) == Path(target.name)
            and f".{target.name}.new." in Path(source).name
        ):
            swapped = True
            target.parent.rename(moved_parent)
            target.parent.symlink_to(redirected, target_is_directory=True)
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(assistant_setup.os, "link", swap_parent_before_link)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert not tuple(redirected.iterdir())
    assert (moved_parent / target.name).read_bytes() == original_bytes
    assert not tuple(moved_parent.glob(f".{target.name}.new.*"))
    assert not tuple(moved_parent.glob(f".{target.name}.backup.*"))


def test_assets_only_rollback_preserves_concurrent_replacement_and_retires_backup(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, _state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    installed = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)
    assert installed.exit_code == 0, installed.output

    changed_root = tmp_path / "changed-assets-for-rollback"
    shutil.copytree(
        REPOSITORY_ROOT / "src" / "yt_insights" / "assistant_assets",
        changed_root,
    )
    first_relative = "skills/youtube-acquire/SKILL.md"
    second_relative = "skills/youtube-acquire/agents/openai.yaml"
    (changed_root / first_relative).write_text("first upgrade\n", encoding="utf-8")
    (changed_root / second_relative).write_text("second upgrade\n", encoding="utf-8")
    monkeypatch.setattr(assistant_setup, "_asset_root", lambda: changed_root)
    first_target = home / ".agents" / first_relative
    manifest_before = (home / OWNERSHIP_MANIFEST).read_bytes()
    original_replace = assistant_setup._replace_managed_file
    calls = 0

    def replace_then_race(
        target: Path,
        content: bytes,
        expected: assistant_setup.FileSnapshot,
        root: assistant_setup.BoundRoot,
    ) -> assistant_setup.ReplacedFile:
        nonlocal calls
        calls += 1
        if calls == 2:
            concurrent = first_target.with_name(".concurrent-replacement")
            concurrent.write_text("concurrent replacement\n", encoding="utf-8")
            os.replace(concurrent, first_target)
            raise OSError("synthetic later upgrade failure")
        return original_replace(target, content, expected, root)

    monkeypatch.setattr(assistant_setup, "_replace_managed_file", replace_then_race)

    result = runner.invoke(cli, _assets_only_arguments("--apply"), env=env)

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert first_target.read_text(encoding="utf-8") == "concurrent replacement\n"
    assert (home / OWNERSHIP_MANIFEST).read_bytes() == manifest_before
    assert not tuple(first_target.parent.glob(f".{first_target.name}.new.*"))
    assert not tuple(first_target.parent.glob(f".{first_target.name}.backup.*"))


def test_assets_only_never_resolves_or_invokes_mcp_tools(
    tmp_path: Path, monkeypatch
) -> None:
    env, _home, _state, _corpus = _environment(tmp_path)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("assets-only mode accessed an MCP dependency")

    monkeypatch.setattr(assistant_setup, "_resolve_executable", unexpected)
    monkeypatch.setattr(assistant_setup, "_client_executables", unexpected)
    monkeypatch.setattr(assistant_setup, "_get_registration", unexpected)
    monkeypatch.setattr(assistant_setup, "_remove_registration", unexpected)
    monkeypatch.setattr(assistant_setup.subprocess, "run", unexpected)

    result = CliRunner().invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "installed"


def test_assets_only_verify_checks_assets_without_reading_mcp_state(tmp_path: Path) -> None:
    env, _home, state, _corpus = _environment(tmp_path)
    runner = CliRunner()
    applied = runner.invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )
    assert applied.exit_code == 0, applied.output

    verified = runner.invoke(
        cli,
        _assets_only_arguments("--verify"),
        env=env,
        catch_exceptions=False,
    )

    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.output)["status"] == "verified"
    assert not (state / "operations.jsonl").exists()


def test_assets_only_conflict_preserves_customized_asset(tmp_path: Path) -> None:
    env, home, state, _corpus = _environment(tmp_path)
    conflict = (
        home / ".agents" / "skills" / "youtube-cumulative-research" / "SKILL.md"
    )
    conflict.parent.mkdir(parents=True)
    conflict.write_text("custom workflow\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "blocked"
    assert payload["conflicts"] == [str(conflict)]
    assert conflict.read_text(encoding="utf-8") == "custom workflow\n"
    assert not state.exists()


def test_assets_only_write_failure_rolls_back_only_files_it_created(
    tmp_path: Path, monkeypatch
) -> None:
    env, home, state, _corpus = _environment(tmp_path)
    original_write = assistant_setup._write_asset
    calls = 0

    def fail_second_write(
        asset: assistant_setup.Asset,
        root: assistant_setup.BoundRoot,
    ) -> assistant_setup.CreatedFile:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic asset failure")
        return original_write(asset, root)

    monkeypatch.setattr(assistant_setup, "_write_asset", fail_second_write)

    result = CliRunner().invoke(
        cli,
        _assets_only_arguments("--apply"),
        env=env,
        catch_exceptions=False,
    )

    assert result.exit_code != 0
    assert json.loads(result.output)["status"] == "rolled_back"
    assert not (home / ".agents").exists()
    assert not (home / ".claude").exists()
    assert not (home / ".codex").exists()
    assert not state.exists()


def test_default_setup_still_requires_data_root(tmp_path: Path) -> None:
    env, home, state, _corpus = _environment(tmp_path)

    result = CliRunner().invoke(
        cli,
        ["setup", "assistants", "--client", "both", "--apply", "--json"],
        env=env,
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "invalid"
    assert "data root" in payload["error"].lower()
    assert not home.exists()
    assert not state.exists()
