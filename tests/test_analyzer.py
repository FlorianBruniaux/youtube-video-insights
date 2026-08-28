from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from yt_insights import analyzer
from yt_insights.analyzer import analyze_video
from yt_insights.config import Config
from yt_insights.cleaner import clean_vtt


INSIGHT_DATA = {
    "subject": "Reliable agent delivery",
    "key_points": ["Measure failures", "Keep decisions observable"],
    "tools": [{"name": "Codex", "context": "Reviews the implementation"}],
    "advice": ["Test the critical paths first"],
    "quotes": ["Observable beats magical."],
}


def test_prepare_transcript_input_reports_long_and_short_usage() -> None:
    long_input = analyzer.prepare_transcript_input("s" * 10_123, max_chars=10_000)
    short_input = analyzer.prepare_transcript_input("public transcript", max_chars=10_000)

    assert long_input.usage == analyzer.TranscriptUsage(
        total_chars=10_123,
        used_chars=10_000,
        max_chars=10_000,
        truncated=True,
    )
    assert long_input.text.startswith("s" * 10_000)
    assert "transcript truncated" in long_input.text
    assert long_input.usage.format_message() == (
        "Transcript input: 10000/10123 characters (truncated)"
    )
    assert "sss" not in long_input.usage.format_message()
    assert short_input.usage == analyzer.TranscriptUsage(
        total_chars=17,
        used_chars=17,
        max_chars=10_000,
        truncated=False,
    )
    assert short_input.text == "public transcript"


def test_analyze_video_reports_usage_only_before_an_llm_call(
    tmp_path: Path, fake_backend_factory
) -> None:
    vtt_path = tmp_path / "long.vtt"
    unique_lines = [f"segment-{i:04d}-" + ("private " * 20) for i in range(80)]
    vtt_path.write_text("WEBVTT\n\n" + "\n".join(unique_lines), encoding="utf-8")
    backend = fake_backend_factory([(json.dumps(INSIGHT_DATA), "end_turn")])
    usages: list[tuple[Path, object]] = []

    result = analyze_video(
        vtt_path,
        tmp_path / "insights",
        backend,
        Config(max_transcript_chars=10_000),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert usages == [
        (
            vtt_path,
            analyzer.TranscriptUsage(
                total_chars=len(clean_vtt(vtt_path)),
                used_chars=10_000,
                max_chars=10_000,
                truncated=True,
            ),
        )
    ]


def test_analyze_video_reports_usage_again_before_retry(
    transcript_path: Path, tmp_path: Path, fake_backend_factory
) -> None:
    backend = fake_backend_factory(
        [("not json", "end_turn"), (json.dumps(INSIGHT_DATA), "end_turn")]
    )
    usages: list[tuple[Path, object]] = []

    result = analyze_video(
        transcript_path,
        tmp_path / "insights",
        backend,
        Config(),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert len(backend.calls) == 2
    assert len(usages) == 2
    assert usages[0] == usages[1]


def test_analyze_video_uses_a_valid_cache_without_calling_the_llm(
    transcript_path: Path, tmp_path: Path, fake_backend_factory
) -> None:
    """Detects a cache-hit regression that needlessly sends a transcript to the LLM."""
    insights_dir = tmp_path / "insights"
    insights_dir.mkdir()
    cache_path = insights_dir / f"{transcript_path.stem}.json"
    cache_path.write_text(json.dumps(INSIGHT_DATA), encoding="utf-8")
    backend = fake_backend_factory()

    usages: list[tuple[Path, object]] = []
    result = analyze_video(
        transcript_path,
        insights_dir,
        backend,
        Config(),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert result.title == "20260223 - Build reliable agents"
    assert result.to_dict() == INSIGHT_DATA
    assert backend.calls == []
    assert usages == []


def test_analyze_video_refuses_to_write_a_truncated_llm_response(
    transcript_path: Path, tmp_path: Path, fake_backend_factory
) -> None:
    """Detects persistence of a response truncated by the model token limit."""
    insights_dir = tmp_path / "insights"
    backend = fake_backend_factory([("{", "max_tokens")])

    with pytest.warns(UserWarning, match="LLM hit max_tokens"):
        result = analyze_video(transcript_path, insights_dir, backend, Config())

    assert result is None
    assert not (insights_dir / f"{transcript_path.stem}.json").exists()
    assert not (insights_dir / f"{transcript_path.stem}.md").exists()
    assert len(backend.calls) == 1


def test_analyze_video_replaces_complete_temporary_files_atomically(
    transcript_path: Path, tmp_path: Path, monkeypatch, fake_backend_factory
) -> None:
    """Detects a regression from atomic insight writes to directly written final files."""
    insights_dir = tmp_path / "insights"
    backend = fake_backend_factory([(json.dumps(INSIGHT_DATA), "end_turn")])
    actual_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def observing_replace(source: Path, destination: Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.exists()
        replacements.append((source_path, destination_path))
        actual_replace(source_path, destination_path)

    monkeypatch.setattr(analyzer.os, "replace", observing_replace)

    result = analyze_video(transcript_path, insights_dir, backend, Config())

    insight_path = insights_dir / f"{transcript_path.stem}.json"
    markdown_path = insights_dir / f"{transcript_path.stem}.md"
    assert result is not None
    assert json.loads(insight_path.read_text(encoding="utf-8")) == INSIGHT_DATA
    assert markdown_path.read_text(encoding="utf-8") == (
        "# 20260223 - Build reliable agents\n\n"
        "**Sujet principal** : Reliable agent delivery\n\n"
        "**Points clés** :\n"
        "- Measure failures\n"
        "- Keep decisions observable\n\n"
        "**Outils / technos** :\n"
        "- **Codex** : Reviews the implementation\n\n"
        "**Conseils actionnables** :\n"
        "- Test the critical paths first\n\n"
        "**Citations notables** :\n"
        "> Observable beats magical.\n"
    )
    assert replacements == [
        (insight_path.with_suffix(".tmp.json"), insight_path),
        (markdown_path.with_suffix(".tmp.md"), markdown_path),
    ]
    assert not insight_path.with_suffix(".tmp.json").exists()
    assert not markdown_path.with_suffix(".tmp.md").exists()
