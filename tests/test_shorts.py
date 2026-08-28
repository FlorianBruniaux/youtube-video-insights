from __future__ import annotations

import json
from pathlib import Path

from yt_insights import analyzer
from yt_insights.config import Config
from yt_insights.shorts import suggest_shorts


def _long_timestamped_vtt(path: Path) -> None:
    blocks = ["WEBVTT", ""]
    for index in range(25):
        blocks.extend(
            [
                f"00:00:{index:02d}.000 --> 00:00:{index:02d}.900",
                f"segment-{index:02d} " + ("sensitive " * 50),
                "",
            ]
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def test_suggest_shorts_reports_truncated_usage_before_calling_llm(
    tmp_path: Path, fake_backend_factory
) -> None:
    vtt_path = tmp_path / "long [nfupYzLjFGc].fr.vtt"
    _long_timestamped_vtt(vtt_path)
    backend = fake_backend_factory([("[]", "end_turn")])
    usages: list[tuple[Path, object]] = []

    result = suggest_shorts(
        vtt_path,
        tmp_path / "insights",
        tmp_path / "shorts",
        backend,
        Config(max_transcript_chars=10_000),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert len(usages) == 1
    assert usages[0][0] == vtt_path
    assert usages[0][1].used_chars == 10_000
    assert usages[0][1].total_chars > 10_000
    assert usages[0][1].truncated is True


def test_suggest_shorts_does_not_report_usage_for_cached_result(
    tmp_path: Path, fake_backend_factory
) -> None:
    vtt_path = tmp_path / "cached [nfupYzLjFGc].fr.vtt"
    _long_timestamped_vtt(vtt_path)
    shorts_dir = tmp_path / "shorts"
    shorts_dir.mkdir()
    (shorts_dir / f"{vtt_path.stem}.json").write_text(json.dumps([]), encoding="utf-8")
    backend = fake_backend_factory()
    usages: list[tuple[Path, object]] = []

    result = suggest_shorts(
        vtt_path,
        tmp_path / "insights",
        shorts_dir,
        backend,
        Config(max_transcript_chars=10_000),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert usages == []
    assert backend.calls == []


def test_suggest_shorts_reports_usage_again_before_retry(
    tmp_path: Path, fake_backend_factory
) -> None:
    vtt_path = tmp_path / "retry [nfupYzLjFGc].fr.vtt"
    _long_timestamped_vtt(vtt_path)
    backend = fake_backend_factory([("not json", "end_turn"), ("[]", "end_turn")])
    usages: list[tuple[Path, object]] = []

    result = suggest_shorts(
        vtt_path,
        tmp_path / "insights",
        tmp_path / "shorts",
        backend,
        Config(max_transcript_chars=10_000),
        on_transcript_usage=lambda path, usage: usages.append((path, usage)),
    )

    assert result is not None
    assert len(backend.calls) == 2
    assert len(usages) == 2
    assert usages[0] == usages[1]
