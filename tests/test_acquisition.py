from __future__ import annotations

from pathlib import Path
import json

import pytest

from yt_insights.acquisition import (
    SourceKind,
    build_acquisition_plan,
    classify_source,
    execute_acquisition,
)
from yt_insights.downloader import DownloadResult, VideoInfo
from yt_insights.paths import DataPaths


@pytest.mark.parametrize(
    ("source", "kind"),
    [
        ("https://www.youtube.com/watch?v=nfupYzLjFGc", SourceKind.VIDEO),
        ("https://youtu.be/nfupYzLjFGc", SourceKind.VIDEO),
        ("https://www.youtube.com/shorts/nfupYzLjFGc", SourceKind.VIDEO),
        ("https://www.youtube.com/playlist?list=PL123", SourceKind.PLAYLIST),
        ("https://www.youtube.com/@example/videos", SourceKind.CHANNEL),
    ],
)
def test_classify_source(source: str, kind: SourceKind) -> None:
    assert classify_source(source) is kind


def test_classify_source_accepts_only_existing_regular_batch_file(tmp_path: Path) -> None:
    batch = tmp_path / "urls.txt"
    batch.write_text("https://youtu.be/nfupYzLjFGc\n", encoding="utf-8")
    assert classify_source(str(batch)) is SourceKind.BATCH

    with pytest.raises(ValueError, match="regular file"):
        classify_source(str(tmp_path))
    with pytest.raises(ValueError, match="does not exist"):
        classify_source(str(tmp_path / "missing.txt"))


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/watch?v=nfupYzLjFGc",
        "https://www.youtube.com/watch?v=nfupYzLjFGc&list=PL123",
        "https://www.youtube.com/feed/subscriptions",
        "bad\x00source",
    ],
)
def test_classify_source_rejects_unsupported_or_ambiguous_input(source: str) -> None:
    with pytest.raises(ValueError):
        classify_source(source)


def test_channel_plan_filters_exact_year_and_reports_missing_date(tmp_path: Path) -> None:
    plan = build_acquisition_plan(
        source="https://www.youtube.com/@example/videos",
        data_paths=DataPaths.from_root(tmp_path),
        slug="example",
        years={2025, 2026},
        discovered=[
            VideoInfo("aaa123DEF45", "Selected", "20260820"),
            VideoInfo("bbb123DEF45", "Old", "20240820"),
            VideoInfo("ccc123DEF45", "Unknown", ""),
        ],
    )

    assert plan.requires_confirmation is True
    assert plan.selected_count == 1
    assert plan.selected_urls == (
        "https://www.youtube.com/watch?v=aaa123DEF45",
    )
    assert plan.output_root == tmp_path.resolve() / "example"
    assert plan.exclusions == (
        "bbb123DEF45: year_not_selected",
        "ccc123DEF45: missing_upload_date",
    )


def test_single_video_plan_uses_flat_inbox_without_confirmation(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("nfupYzLjFGc", "Reliable agents", "20260820")

    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=paths,
        language="fr",
        discovered=[video],
    )

    assert plan.requires_confirmation is False
    assert plan.output_root == paths.root
    assert plan.transcripts_dir == paths.transcripts
    assert plan.insights_dir == paths.insights


def test_plan_deduplicates_video_identity(tmp_path: Path) -> None:
    video = VideoInfo("nfupYzLjFGc", "Reliable agents", "20260820")
    plan = build_acquisition_plan(
        source="https://www.youtube.com/playlist?list=PL123",
        data_paths=DataPaths.from_root(tmp_path),
        discovered=[video, video],
    )

    assert plan.selected_count == 1
    assert plan.exclusions == ("nfupYzLjFGc: duplicate_video",)


def test_execute_counts_cached_and_failed_videos_with_identity(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    ready = VideoInfo("aaa123DEF45", "Cached", "20260820")
    failed = VideoInfo("bbb123DEF45", "Unavailable", "20260819")
    plan = build_acquisition_plan(
        source="https://www.youtube.com/playlist?list=PL123",
        data_paths=paths,
        slug="playlist",
        discovered=[ready, failed],
    )
    plan.transcripts_dir.mkdir(parents=True)
    cached = plan.transcripts_dir / "20260820 - Cached [aaa123DEF45].fr.vtt"
    cached.write_text("WEBVTT\n", encoding="utf-8")

    def fake_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        if "bbb123DEF45" in source:
            return DownloadResult(errors=["ERROR: subtitles unavailable"], returncode=1)
        return DownloadResult(vtt_files=[cached])

    report = execute_acquisition(
        plan,
        download=fake_download,
        refresh_indexes=False,
    )

    assert report.selected == 2
    assert report.transcripts_ready == 1
    assert report.insights_ready == 0
    assert report.exit_code == 4
    assert report.failures == (
        "bbb123DEF45 (Unavailable): ERROR: subtitles unavailable",
    )


def test_execute_all_failed_exits_one(tmp_path: Path) -> None:
    video = VideoInfo("aaa123DEF45", "No subtitles", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=DataPaths.from_root(tmp_path),
        discovered=[video],
    )

    report = execute_acquisition(
        plan,
        download=lambda *args, **kwargs: DownloadResult(
            errors=["ERROR: missing subtitles"], returncode=1
        ),
        refresh_indexes=False,
    )

    assert report.exit_code == 1
    assert report.transcripts_ready == 0


def test_execute_refreshes_catalog_and_search_index_for_flat_inbox(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("aaa123DEF45", "Indexed source", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=paths,
        discovered=[video],
    )

    def fake_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        output_dir.mkdir(parents=True)
        vtt = output_dir / "20260820 - Indexed source [aaa123DEF45].fr.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nReliable evidence\n",
            encoding="utf-8",
        )
        (output_dir / "20260820 - Indexed source [aaa123DEF45].info.json").write_text(
            json.dumps(
                {
                    "id": "aaa123DEF45",
                    "channel_id": "UCStableChannel",
                    "channel": "Stable Channel",
                }
            ),
            encoding="utf-8",
        )
        return DownloadResult(vtt_files=[vtt])

    report = execute_acquisition(plan, download=fake_download)

    assert report.exit_code == 0
    assert paths.catalog_database.is_file()
    assert paths.search_database.is_file()


def test_execute_counts_cached_insight_without_resolving_backend(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("aaa123DEF45", "Cached insight", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=paths,
        analyze=True,
        discovered=[video],
    )
    paths.transcripts.mkdir(parents=True)
    paths.insights.mkdir(parents=True)
    vtt = paths.transcripts / "20260820 - Cached insight [aaa123DEF45].fr.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    (paths.insights / f"{vtt.stem}.json").write_text("{}", encoding="utf-8")

    report = execute_acquisition(
        plan,
        backend_resolver=lambda config: (_ for _ in ()).throw(
            AssertionError("cached insights must not resolve a backend")
        ),
        refresh_indexes=False,
    )

    assert report.exit_code == 0
    assert report.insights_ready == 1
