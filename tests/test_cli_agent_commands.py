from __future__ import annotations

import json
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
    "run",
    "search",
    "setup",
    "suggest-shorts",
}
VIDEO_ID = "nfupYzLjFGc"


def test_root_cli_registers_legacy_and_agent_facing_commands() -> None:
    assert set(cli.commands) == EXPECTED_COMMANDS


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
