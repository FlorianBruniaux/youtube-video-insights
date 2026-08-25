from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from yt_insights.cli import cli


def test_help_lists_the_insight_pipeline_commands() -> None:
    """Detects removal of the public CLI help for the insight pipeline."""
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "yt-insights run SOURCE" in result.output
    assert "yt-insights report" in result.output


def test_list_renders_videos_from_the_discovery_adapter(monkeypatch) -> None:
    """Detects a CLI list regression after replacing the discovery adapter."""
    from yt_insights import downloader

    monkeypatch.setattr(
        downloader.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=(
                "20260223|Build reliable agents|nfupYzLjFGc\n"
                "20260201|Earlier work|rAfAnJcuymo\n"
            ),
            stderr="",
        ),
    )

    result = CliRunner().invoke(cli, ["list", "https://www.youtube.com/@example"])

    assert result.exit_code == 0
    assert result.output == (
        "Fetching video list from https://www.youtube.com/@example ...\n\n"
        "    #  Date        Title\n"
        "  ---  ----------  -----\n"
        "    1.  2026-02-23  Build reliable agents\n"
        "    2.  2026-02-01  Earlier work\n\n"
        "2 video(s) found.\n"
    )


def test_run_skip_download_uses_cached_vtts_and_writes_reports(
    tmp_path: Path, sample_fr_vtt: Path, monkeypatch, fake_backend_factory
) -> None:
    """Detects a cached-transcript pipeline regression without a network download."""
    from yt_insights import cli as cli_module
    from yt_insights import config as config_module
    from yt_insights import downloader

    output_dir = tmp_path / "output"
    transcripts_dir = output_dir / "transcripts"
    insights_dir = output_dir / "insights"
    transcripts_dir.mkdir(parents=True)
    vtt_path = transcripts_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt"
    vtt_path.write_text(sample_fr_vtt.read_text(encoding="utf-8"), encoding="utf-8")
    analysis_backend = fake_backend_factory(
        [
            (
                json.dumps(
                    {
                        "subject": "Reliable agent delivery",
                        "key_points": ["Measure failures"],
                        "tools": [{"name": "Codex", "context": "Review"}],
                        "advice": ["Test critical paths"],
                        "quotes": [],
                    }
                ),
                "end_turn",
            )
        ]
    )
    report_backend = fake_backend_factory([("A focused narrative.", "end_turn")])
    backends = iter([analysis_backend, report_backend])
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing-config.toml")
    monkeypatch.setattr(cli_module, "resolve_backend", lambda resolved_config: next(backends))
    def fail_if_downloaded(*args, **kwargs) -> None:
        raise AssertionError("--skip-download unexpectedly called download_subtitles().")

    monkeypatch.setattr(downloader, "download_subtitles", fail_if_downloaded)

    result = CliRunner().invoke(
        cli,
        ["run", "https://www.youtube.com/watch?v=nfupYzLjFGc", "--skip-download", "--output-dir", str(output_dir)],
        env={"YT_INSIGHTS_MODEL": "test-model"},
    )

    report_path = insights_dir / "AGGREGATE_REPORT.md"
    assert result.exit_code == 0
    assert result.output == (
        "Found 1 existing VTT file(s).\n\n"
        "Analyzing 1 video(s) with model 'test-model' ...\n"
        "  1 insight(s) generated:\n"
        f"    {insights_dir / '20260223 - Build reliable agents [nfupYzLjFGc].fr.md'}\n\n"
        "Generating aggregate report ...\n"
        f"  Aggregate  → {report_path}\n"
        f"  Full       → {insights_dir / 'FULL_REPORT.md'}\n"
        "Done.\n"
    )
    assert json.loads(
        (insights_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.json").read_text(encoding="utf-8")
    ) == {
        "subject": "Reliable agent delivery",
        "key_points": ["Measure failures"],
        "tools": [{"name": "Codex", "context": "Review"}],
        "advice": ["Test critical paths"],
        "quotes": [],
    }
    assert report_path.read_text(encoding="utf-8") == (
        "# Rapport agrégé : 1 vidéos\n\n"
        "## Stack et outils cités\n\n"
        "- **Codex** (1)\n\n"
        "---\n\n"
        "A focused narrative.\n"
    )
    assert analysis_backend.closed is True
    assert report_backend.closed is True


def test_report_renders_existing_insights_without_network(
    tmp_path: Path, monkeypatch, fake_backend_factory
) -> None:
    """Detects a report-command regression when its LLM adapter is replaced."""
    from yt_insights import cli as cli_module
    from yt_insights import config as config_module

    insights_dir = tmp_path / "insights"
    insights_dir.mkdir()
    (insights_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.json").write_text(
        json.dumps(
            {
                "subject": "Reliable agent delivery",
                "key_points": ["Measure failures"],
                "tools": [{"name": "Codex", "context": "Review"}],
                "advice": ["Test critical paths"],
                "quotes": [],
            }
        ),
        encoding="utf-8",
    )
    backend = fake_backend_factory([("A focused narrative.", "end_turn")])
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing-config.toml")
    monkeypatch.setattr(cli_module, "resolve_backend", lambda resolved_config: backend)

    result = CliRunner().invoke(
        cli,
        ["report"],
        env={
            "YT_INSIGHTS_INSIGHTS_DIR": str(insights_dir),
            "YT_INSIGHTS_MODEL": "test-model",
        },
    )

    report_path = insights_dir / "AGGREGATE_REPORT.md"
    assert result.exit_code == 0
    assert result.output == f"Report written to {report_path}\n"
    assert report_path.read_text(encoding="utf-8") == (
        "# Rapport agrégé : 1 vidéos\n\n"
        "## Stack et outils cités\n\n"
        "- **Codex** (1)\n\n"
        "---\n\n"
        "A focused narrative.\n"
    )
    assert backend.closed is True
