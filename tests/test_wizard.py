from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from yt_insights.backends import BackendIdentity, ResolvedBackend
from yt_insights.cli import cli
from yt_insights.config import Config


def test_wizard_prints_public_identity_after_resolving_backend(
    tmp_path, monkeypatch, fake_backend_factory, capsys
) -> None:
    """Detects a wizard path that resolves a backend without identifying it."""
    from yt_insights import analyzer, backends, downloader, wizard

    vtt = tmp_path / "talk.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    backend = fake_backend_factory()
    resolved = ResolvedBackend(
        backend,
        BackendIdentity("ollama", "http://127.0.0.1:11434/v1", "qwen3:8b"),
    )
    monkeypatch.setattr(
        downloader,
        "download_subtitles",
        lambda source, target: SimpleNamespace(errors=[], vtt_files=[vtt]),
    )
    def fake_analyze_all(*args, **kwargs):
        from yt_insights.analyzer import TranscriptUsage

        kwargs["on_transcript_usage"](
            vtt,
            TranscriptUsage(12_345, 10_000, 10_000, True),
        )
        return []

    monkeypatch.setattr(analyzer, "analyze_all", fake_analyze_all)
    monkeypatch.setattr(backends, "resolve_backend", lambda config: resolved)

    wizard._run_insights("https://example.test", Config(transcripts_dir=tmp_path))

    captured = capsys.readouterr()
    assert "Backend resolved: backend=ollama endpoint=http://127.0.0.1:11434/v1 model=qwen3:8b" in captured.out
    assert "Analyse de 1 vidéo(s) avec 'qwen3:8b'" in captured.out
    assert "Transcript input: 10000/12345 characters (truncated)" in captured.out
    assert "claude-haiku-4-5" not in captured.out + captured.err
    assert backend.closed is True


def test_wizard_never_prints_endpoint_secrets(
    tmp_path, monkeypatch, fake_backend_factory, capsys
) -> None:
    """Protects wizard stdout and stderr even if an adapter returns an unsafe identity."""
    from yt_insights import analyzer, backends, downloader, wizard

    vtt = tmp_path / "talk.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    backend = fake_backend_factory()
    resolved = ResolvedBackend(
        backend,
        BackendIdentity(
            "api",
            "https://alice:password-secret@example.test/v1?token=query-secret#fragment-secret",
            "safe-model",
        ),
    )
    monkeypatch.setattr(
        downloader,
        "download_subtitles",
        lambda source, target: SimpleNamespace(errors=[], vtt_files=[vtt]),
    )
    monkeypatch.setattr(analyzer, "analyze_all", lambda *args, **kwargs: [])
    monkeypatch.setattr(backends, "resolve_backend", lambda config: resolved)

    wizard._run_insights("https://example.test", Config(transcripts_dir=tmp_path))

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert "endpoint=https://example.test/v1" in captured.out
    assert "password-secret" not in combined
    assert "query-secret" not in combined
    assert "fragment-secret" not in combined


@pytest.mark.parametrize(
    "command",
    [
        ["run", "--help"],
        ["report", "--help"],
        ["suggest-shorts", "--help"],
        ["interactive", "--help"],
        ["config", "show", "--help"],
    ],
)
def test_every_llm_cli_surface_exposes_backend_selection(command: list[str]) -> None:
    result = CliRunner().invoke(cli, command)

    assert result.exit_code == 0, result.output
    assert "--backend" in result.output


def test_config_show_reports_backend_without_runtime_probe(monkeypatch) -> None:
    from yt_insights import backends

    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("config show probed a backend"),
    )

    result = CliRunner().invoke(
        cli,
        ["config", "show", "--backend", "mlx", "--model", "mlx/test-model"],
    )

    assert result.exit_code == 0, result.output
    assert "backend" in result.output
    assert "mlx" in result.output


def test_wizard_backend_choices_contain_only_detected_or_configured_routes(
    monkeypatch,
) -> None:
    from yt_insights import backends, wizard

    monkeypatch.setattr(
        backends,
        "available_backend_routes",
        lambda config: ("ollama", "anthropic"),
    )

    assert wizard._backend_choices(Config()) == [
        {"name": "Ollama local", "value": "ollama"},
        {"name": "Anthropic API", "value": "anthropic"},
    ]


def test_tty_wizard_applies_selected_backend_without_loading_a_model(
    monkeypatch,
) -> None:
    from yt_insights import wizard

    selected: list[str] = []
    monkeypatch.setattr(wizard, "_is_tty", lambda: True)
    monkeypatch.setattr(wizard, "_prompt_backend", lambda config: "mlx")
    monkeypatch.setattr(
        wizard,
        "_run_insights",
        lambda source, config: selected.append(config.backend),
    )

    wizard.run_wizard(
        action="insights",
        source="https://example.test/video",
        duration="any",
        platform="none",
        output_format="mp4",
    )

    assert selected == ["mlx"]
