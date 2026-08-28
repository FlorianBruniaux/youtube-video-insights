from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from yt_insights.cli import cli
from yt_insights.config import Config
from yt_insights.backends import BackendIdentity, ResolvedBackend
from yt_insights.backends.base import BackendUnavailableError


def _resolved_fake(backend, *, model: str = "qwen3:8b") -> ResolvedBackend:
    return ResolvedBackend(
        backend,
        BackendIdentity("ollama", "http://127.0.0.1:11434/v1", model),
    )


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
    backends = iter([_resolved_fake(analysis_backend), _resolved_fake(report_backend)])
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
        "Resolved backend: backend=ollama endpoint=http://127.0.0.1:11434/v1 model=qwen3:8b\n"
        "Found 1 existing VTT file(s).\n\n"
        "Analyzing 1 video(s) with model 'qwen3:8b' ...\n"
        "Transcript input: 328/328 characters\n"
        "  1 insight(s) generated:\n"
        f"    {insights_dir / '20260223 - Build reliable agents [nfupYzLjFGc].fr.md'}\n\n"
        "Generating aggregate report ...\n"
        "Resolved backend: backend=ollama endpoint=http://127.0.0.1:11434/v1 model=qwen3:8b\n"
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
    assert "test-model" not in result.output


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
    monkeypatch.setattr(cli_module, "resolve_backend", lambda resolved_config: _resolved_fake(backend))

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
    assert result.output == (
        "Resolved backend: backend=ollama endpoint=http://127.0.0.1:11434/v1 "
        "model=qwen3:8b\n"
        f"Report written to {report_path}\n"
    )
    assert report_path.read_text(encoding="utf-8") == (
        "# Rapport agrégé : 1 vidéos\n\n"
        "## Stack et outils cités\n\n"
        "- **Codex** (1)\n\n"
        "---\n\n"
        "A focused narrative.\n"
    )
    assert backend.closed is True


def test_config_show_labels_values_as_unprobed_configuration() -> None:
    """Prevents config display from claiming a backend was resolved at runtime."""
    result = CliRunner().invoke(cli, ["config", "show", "--model", "qwen3:8b"])

    assert result.exit_code == 0
    assert "yt-insights effective configuration (no runtime backend probe)" in result.output
    assert "resolved configuration" not in result.output


def test_config_show_never_prints_endpoint_url_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    """Protect config diagnostics from userinfo, query and fragment canaries."""
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    canaries = (
        "USERINFO-CANARY",
        "PASSWORD-CANARY",
        "QUERY-CANARY",
        "FRAGMENT-CANARY",
    )
    endpoint = (
        "HTTPS://USERINFO-CANARY:PASSWORD-CANARY@[2001:db8::1]:8443/v1/chat"
        "?token=QUERY-CANARY#FRAGMENT-CANARY"
    )

    result = CliRunner().invoke(
        cli, ["config", "show", "--base-url", endpoint]
    )

    assert result.exit_code == 0
    assert "https://[2001:db8::1]:8443/v1/chat" in result.output
    assert all(canary not in result.output for canary in canaries)


def test_run_closes_report_backend_when_report_generation_fails(
    tmp_path: Path, sample_fr_vtt: Path, monkeypatch, fake_backend_factory
) -> None:
    """Detects leaking the second backend after report generation fails."""
    from yt_insights import analyzer, cli as cli_module, config as config_module, reporter

    output_dir = tmp_path / "output"
    transcripts = output_dir / "transcripts"
    insights = output_dir / "insights"
    transcripts.mkdir(parents=True)
    insights.mkdir()
    (transcripts / "talk [nfupYzLjFGc].fr.vtt").write_text(sample_fr_vtt.read_text(), encoding="utf-8")
    insight_path = insights / "talk.json"
    insight_path.with_suffix(".md").write_text("# insight", encoding="utf-8")
    analysis_backend = fake_backend_factory()
    report_backend = fake_backend_factory()
    backends = iter([_resolved_fake(analysis_backend), _resolved_fake(report_backend)])
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(cli_module, "resolve_backend", lambda config: next(backends))
    monkeypatch.setattr(analyzer, "analyze_all", lambda *args, **kwargs: [SimpleNamespace(insight_path=insight_path)])
    monkeypatch.setattr(reporter, "generate_report", lambda *args, **kwargs: (_ for _ in ()).throw(BackendUnavailableError("offline")))

    result = CliRunner().invoke(cli, ["run", "source", "--skip-download", "--output-dir", str(output_dir)])

    assert result.exit_code == 0
    assert "Warning: could not generate report" in result.output
    assert analysis_backend.closed is True
    assert report_backend.closed is True


def test_run_never_prints_endpoint_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    """Protects both Click stdout and stderr from configured URL secrets."""
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "source",
            "--skip-download",
            "--output-dir",
            str(tmp_path / "output"),
            "--base-url",
            "https://alice:password-secret@example.test/v1?token=query-secret#fragment-secret",
        ],
    )

    assert result.exit_code == 1
    assert "endpoint=https://example.test/v1" in result.output
    assert "password-secret" not in result.output
    assert "query-secret" not in result.output
    assert "fragment-secret" not in result.output


def test_suggest_shorts_prints_only_the_resolved_model(
    tmp_path: Path, sample_fr_vtt: Path, monkeypatch, fake_backend_factory
) -> None:
    """Prevents a stale configured model from contradicting the resolved identity."""
    from yt_insights import cli as cli_module
    from yt_insights import config as config_module
    from yt_insights import shorts

    output_dir = tmp_path / "output"
    transcripts = output_dir / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / "talk [nfupYzLjFGc].fr.vtt").write_text(
        sample_fr_vtt.read_text(encoding="utf-8"), encoding="utf-8"
    )
    backend = fake_backend_factory()
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(
        cli_module,
        "resolve_backend",
        lambda config: _resolved_fake(backend, model="detected-local-model"),
    )
    def fake_suggest_all(*args, **kwargs):
        from yt_insights.analyzer import TranscriptUsage

        kwargs["on_transcript_usage"](
            transcripts / "talk [nfupYzLjFGc].fr.vtt",
            TranscriptUsage(12_345, 10_000, 10_000, True),
        )
        return []

    monkeypatch.setattr(shorts, "suggest_all", fake_suggest_all)

    result = CliRunner().invoke(
        cli,
        ["suggest-shorts", "--output-dir", str(output_dir)],
        env={"YT_INSIGHTS_MODEL": "stale-config-model"},
    )

    assert result.exit_code == 0
    assert "model 'detected-local-model'" in result.output
    assert "Transcript input: 10000/12345 characters (truncated)" in result.output
    assert "stale-config-model" not in result.output
