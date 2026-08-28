from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yt_insights import cli_acquire
from yt_insights.acquisition import AcquisitionReport
from yt_insights.downloader import VideoInfo, VideoListResult


def test_acquire_help_exposes_backend_choice() -> None:
    result = CliRunner().invoke(cli_acquire.acquire, ["--help"])

    assert result.exit_code == 0, result.output
    assert "--backend" in result.output
    for backend in ("auto", "ollama", "mlx", "cc-bridge", "anthropic", "openai"):
        assert backend in result.output


def test_acquire_propagates_explicit_backend_to_analysis_execution(
    tmp_path: Path, monkeypatch
) -> None:
    observed: dict[str, object] = {}
    monkeypatch.setattr(
        cli_acquire,
        "fetch_video_list",
        lambda *args, **kwargs: VideoListResult(
            videos=[VideoInfo("aaa123DEF45", "One", "20260820")]
        ),
    )

    def fake_execute(plan, *, config, cookies_from_browser=None):
        observed["backend"] = config.backend
        observed["analyze"] = plan.analyze
        return AcquisitionReport(
            selected=1,
            transcripts_ready=1,
            insights_ready=1,
            failures=(),
        )

    monkeypatch.setattr(cli_acquire, "execute_acquisition", fake_execute)

    result = CliRunner().invoke(
        cli_acquire.acquire,
        [
            "https://youtu.be/aaa123DEF45",
            "--analyze",
            "--backend",
            "mlx",
            "--data-root",
            str(tmp_path / "corpus"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert observed == {"backend": "mlx", "analyze": True}


def test_dry_run_discovers_but_never_executes_or_writes(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "corpus"
    monkeypatch.setattr(
        cli_acquire,
        "fetch_video_list",
        lambda *args, **kwargs: VideoListResult(
            videos=[VideoInfo("aaa123DEF45", "One", "20260820")]
        ),
    )
    monkeypatch.setattr(
        cli_acquire,
        "execute_acquisition",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = CliRunner().invoke(
        cli_acquire.acquire,
        [
            "https://www.youtube.com/@example/videos",
            "--slug",
            "example",
            "--data-root",
            str(root),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["selected_count"] == 1
    assert not root.exists()


def test_multi_source_without_yes_prints_plan_and_exits_three(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli_acquire,
        "fetch_video_list",
        lambda *args, **kwargs: VideoListResult(
            videos=[VideoInfo("aaa123DEF45", "One", "20260820")]
        ),
    )

    result = CliRunner().invoke(
        cli_acquire.acquire,
        [
            "https://www.youtube.com/@example/videos",
            "--slug",
            "example",
            "--data-root",
            str(tmp_path / "corpus"),
            "--json",
        ],
    )

    assert result.exit_code == 3
    assert json.loads(result.output)["requires_confirmation"] is True


def test_yes_executes_and_propagates_partial_exit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli_acquire,
        "fetch_video_list",
        lambda *args, **kwargs: VideoListResult(
            videos=[VideoInfo("aaa123DEF45", "One", "20260820")]
        ),
    )
    monkeypatch.setattr(
        cli_acquire,
        "execute_acquisition",
        lambda *args, **kwargs: AcquisitionReport(
            selected=1,
            transcripts_ready=0,
            insights_ready=0,
            failures=("aaa123DEF45 (One): unavailable",),
        ),
    )

    result = CliRunner().invoke(
        cli_acquire.acquire,
        [
            "https://www.youtube.com/playlist?list=PL123",
            "--slug",
            "playlist",
            "--data-root",
            str(tmp_path / "corpus"),
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 1
    assert json.loads(result.output)["failures"][0].startswith("aaa123DEF45")


def test_invalid_years_is_click_usage_error() -> None:
    result = CliRunner().invoke(
        cli_acquire.acquire,
        ["https://youtu.be/nfupYzLjFGc", "--years", "2025,nope"],
    )
    assert result.exit_code == 2


def test_batch_cli_discovers_snapshot_urls_never_original_path(
    tmp_path: Path, monkeypatch
) -> None:
    batch = tmp_path / "urls.txt"
    batch.write_text("https://youtu.be/aaa123DEF45\n", encoding="utf-8")
    calls: list[str] = []

    def fake_fetch(source: str, **kwargs: object) -> VideoListResult:
        calls.append(source)
        return VideoListResult(
            videos=[VideoInfo("aaa123DEF45", "Snapshot", "20260820")]
        )

    monkeypatch.setattr(cli_acquire, "fetch_video_list", fake_fetch)
    result = CliRunner().invoke(
        cli_acquire.acquire,
        [str(batch), "--dry-run", "--json", "--data-root", str(tmp_path / "corpus")],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["https://youtu.be/aaa123DEF45"]
    assert json.loads(result.output)["selected_urls"] == [
        "https://youtu.be/aaa123DEF45"
    ]
