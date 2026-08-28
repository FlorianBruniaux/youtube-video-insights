from __future__ import annotations

from types import SimpleNamespace

from yt_insights.backends import BackendIdentity, ResolvedBackend
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
