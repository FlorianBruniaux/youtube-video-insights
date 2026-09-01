from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from yt_insights import cli_acquire, cli_doctor
from yt_insights.cli import cli
from yt_insights.doctor import CheckResult, DoctorReport
from yt_insights.downloader import VideoInfo, VideoListResult

EXPECTED_COMMANDS = {
    "acquire",
    "catalog",
    "config",
    "doctor",
    "export",
    "generate-short",
    "index",
    "interactive",
    "list",
    "report",
    "research",
    "run",
    "search",
    "serve",
    "setup",
    "suggest-shorts",
}
VIDEO_ID = "nfupYzLjFGc"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_COMMANDS = {
    "acquire",
    "approve",
    "cancel",
    "candidates",
    "decide",
    "discover",
    "export",
    "retry",
    "start",
    "status",
}


def test_root_cli_registers_legacy_and_agent_facing_commands() -> None:
    assert set(cli.commands) == EXPECTED_COMMANDS


def test_research_cli_exposes_the_complete_cumulative_workflow_without_running_it() -> None:
    research = cli.commands["research"]

    assert set(research.commands) == RESEARCH_COMMANDS

    runner = CliRunner()
    group_help = runner.invoke(cli, ["research", "--help"])
    export_help = runner.invoke(cli, ["research", "export", "--help"])

    assert group_help.exit_code == 0, group_help.output
    assert export_help.exit_code == 0, export_help.output
    assert "SESSION_ID" in export_help.output
    assert "--output" in export_help.output
    assert "--force" in export_help.output
    assert "--json" in export_help.output


def test_setup_assistants_help_exposes_the_assets_only_contract() -> None:
    result = CliRunner().invoke(cli, ["setup", "assistants", "--help"])

    assert result.exit_code == 0, result.output
    assert "--assets-only" in result.output
    assert "without inspecting MCP registrations" in " ".join(result.output.split())


def test_wheel_smoke_requires_the_installed_cumulative_research_skill(
    tmp_path: Path,
) -> None:
    script = REPOSITORY_ROOT / "scripts" / "smoke_wheel.py"
    specification = importlib.util.spec_from_file_location("smoke_wheel", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    home = tmp_path / "home"
    skill = (
        home
        / ".agents"
        / "skills"
        / "youtube-cumulative-research"
        / "SKILL.md"
    )
    skill.parent.mkdir(parents=True)
    skill.write_text("# Installed cumulative research\n", encoding="utf-8")

    assert module._verify_cumulative_research_skill(home) == skill

    skill.unlink()
    try:
        module._verify_cumulative_research_skill(home)
    except module.SmokeFailure as error:
        assert "cumulative research skill" in str(error)
    else:
        raise AssertionError("the wheel smoke accepted a missing cumulative skill")


def test_wheel_smoke_fake_assistant_client_records_any_invocation(
    tmp_path: Path,
) -> None:
    script = REPOSITORY_ROOT / "scripts" / "smoke_wheel.py"
    specification = importlib.util.spec_from_file_location("smoke_wheel", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    marker = tmp_path / "invoked.log"
    fake = module._write_fail_if_called_client(tmp_path / "bin", "claude")

    result = subprocess.run(
        [str(fake)],
        env={"YT_INSIGHTS_FAKE_CLIENT_LOG": str(marker)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 99
    assert marker.read_text(encoding="utf-8") == "claude\n"


def test_root_doctor_runs_the_registered_adapter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli_doctor,
        "inspect_runtime",
        lambda *args, **kwargs: DoctorReport(
            str(tmp_path.resolve()),
            (CheckResult("yt-dlp", "pass", "available"),),
        ),
    )

    result = CliRunner().invoke(cli, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data_root"] == str(tmp_path.resolve())


def test_root_acquire_runs_a_dry_run_without_writes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "corpus"
    monkeypatch.setattr(
        cli_acquire,
        "fetch_video_list",
        lambda *args, **kwargs: VideoListResult(
            videos=[VideoInfo(VIDEO_ID, "Reliable agents", "20260820")]
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "acquire",
            f"https://youtu.be/{VIDEO_ID}",
            "--data-root",
            str(root),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["selected_count"] == 1
    assert not root.exists()


def test_root_export_writes_a_source_backed_markdown_file(tmp_path: Path) -> None:
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    stem = f"Reliable agents [{VIDEO_ID}]"
    (transcripts / f"{stem}.fr.vtt").write_text(
        "WEBVTT\n\n00:00:10.000 --> 00:00:12.000\nAgent source\n",
        encoding="utf-8",
    )
    (transcripts / f"{stem}.info.json").write_text(
        json.dumps(
            {
                "id": VIDEO_ID,
                "title": "Reliable agents",
                "channel": "Stable Channel",
                "channel_id": "UCStableChannel123",
            }
        ),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "export",
            "video",
            VIDEO_ID,
            "--data-root",
            str(tmp_path),
            "--lang",
            "fr",
            "--format",
            "md",
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    exported = Path(json.loads(result.output)["path"])
    assert exported.is_file()
    assert "00:00:10" in exported.read_text(encoding="utf-8")
