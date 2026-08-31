from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights.acquisition import (
    AcquisitionPlan,
    SourceKind,
    build_acquisition_plan,
    classify_source,
    execute_acquisition,
    read_batch_snapshot,
)
from yt_insights.downloader import DownloadResult, VideoInfo
from yt_insights.paths import DataPaths


def _assert_catalog_lock_held(lock_path: Path) -> None:
    descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)


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


def test_batch_snapshot_is_bounded_and_accepts_only_video_urls(tmp_path: Path) -> None:
    batch = tmp_path / "urls.txt"
    batch.write_text(
        "https://youtu.be/nfupYzLjFGc\n"
        "https://www.youtube.com/watch?v=aaa123DEF45\n",
        encoding="utf-8",
    )
    assert read_batch_snapshot(batch) == (
        "https://youtu.be/nfupYzLjFGc",
        "https://www.youtube.com/watch?v=aaa123DEF45",
    )

    batch.write_text("https://www.youtube.com/playlist?list=PL123\n", encoding="utf-8")
    with pytest.raises(ValueError, match="video URLs"):
        read_batch_snapshot(batch)

    batch.write_bytes(b"https://youtu.be/nfupYzLjFGc\x00\n")
    with pytest.raises(ValueError, match="NUL"):
        read_batch_snapshot(batch)


def test_batch_snapshot_rejects_symlink_and_excessive_lines(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("https://youtu.be/nfupYzLjFGc\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="regular file"):
        read_batch_snapshot(link)

    target.write_text("https://youtu.be/nfupYzLjFGc\n" * 1001, encoding="utf-8")
    with pytest.raises(ValueError, match="line"):
        read_batch_snapshot(target)


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


def test_plan_rejects_output_override_outside_root_or_symlink(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    outside = tmp_path / "outside"
    outside.mkdir()
    unsafe_paths = replace(DataPaths.from_root(root), transcripts=outside)
    video = VideoInfo("nfupYzLjFGc", "Reliable agents", "20260820")
    with pytest.raises(ValueError, match="data root"):
        build_acquisition_plan(
            source=video.watch_url,
            data_paths=unsafe_paths,
            discovered=[video],
        )

    root.mkdir()
    (root / "transcripts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        build_acquisition_plan(
            source=video.watch_url,
            data_paths=DataPaths.from_root(root),
            discovered=[video],
        )


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
        "bbb123DEF45 (Unavailable): ERROR: subtitles unavailable; "
        "yt-dlp exited with status 1",
    )


def test_execute_reports_each_video_status_and_source_hash(tmp_path: Path) -> None:
    import yt_insights.acquisition as acquisition

    paths = DataPaths.from_root(tmp_path / "corpus")
    videos = (
        VideoInfo("aaa123DEF45", "Cached", "20260820"),
        VideoInfo("bbb123DEF45", "Acquired", "20260819"),
        VideoInfo("ccc123DEF45", "No transcript", "20260818"),
        VideoInfo("ddd123DEF45", "Retryable", "20260817"),
    )
    plan = build_acquisition_plan(
        source="https://www.youtube.com/playlist?list=PL123",
        data_paths=paths,
        slug="playlist",
        discovered=videos,
    )
    plan.transcripts_dir.mkdir(parents=True)
    cached_content = b"WEBVTT\nCached evidence\n"
    (plan.transcripts_dir / "20260820 - Cached [aaa123DEF45].fr.vtt").write_bytes(
        cached_content
    )
    acquired_content = b"WEBVTT\nAcquired evidence\n"

    def fake_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        if "bbb123DEF45" in source:
            acquired = output_dir / "20260819 - Acquired [bbb123DEF45].fr.vtt"
            acquired.write_bytes(acquired_content)
            return DownloadResult(vtt_files=[acquired])
        if "ccc123DEF45" in source:
            return DownloadResult()
        return DownloadResult(errors=["ERROR: temporary failure"], returncode=1)

    report = execute_acquisition(plan, download=fake_download, refresh_indexes=False)

    assert report.items == (
        acquisition.AcquisitionItemReport(
            "aaa123DEF45",
            acquisition.AcquisitionItemStatus.ALREADY_PRESENT,
            source_sha256=hashlib.sha256(cached_content).hexdigest(),
        ),
        acquisition.AcquisitionItemReport(
            "bbb123DEF45",
            acquisition.AcquisitionItemStatus.ACQUIRED,
            source_sha256=hashlib.sha256(acquired_content).hexdigest(),
        ),
        acquisition.AcquisitionItemReport(
            "ccc123DEF45",
            acquisition.AcquisitionItemStatus.NO_TRANSCRIPT,
            error_code="no_transcript",
        ),
        acquisition.AcquisitionItemReport(
            "ddd123DEF45",
            acquisition.AcquisitionItemStatus.FAILED_RETRYABLE,
            error_code="download_failed",
        ),
    )
    assert report.to_dict()["items"] == [
        {
            "video_id": "aaa123DEF45",
            "status": "already_present",
            "error_code": None,
            "source_sha256": hashlib.sha256(cached_content).hexdigest(),
        },
        {
            "video_id": "bbb123DEF45",
            "status": "acquired",
            "error_code": None,
            "source_sha256": hashlib.sha256(acquired_content).hexdigest(),
        },
        {
            "video_id": "ccc123DEF45",
            "status": "no_transcript",
            "error_code": "no_transcript",
            "source_sha256": None,
        },
        {
            "video_id": "ddd123DEF45",
            "status": "failed_retryable",
            "error_code": "download_failed",
            "source_sha256": None,
        },
    ]


def test_execute_rejects_cache_directory_swapped_to_symlink(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("aaa123DEF45", "Unsafe cache", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=paths,
        discovered=[video],
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.root.mkdir()
    paths.transcripts.symlink_to(outside, target_is_directory=True)

    report = execute_acquisition(plan, refresh_indexes=False)

    assert report.exit_code == 1
    assert report.transcripts_ready == 0
    assert report.failures[0].startswith("aaa123DEF45 (Unsafe cache):")
    assert "symlink" in report.failures[0]


def test_cache_file_swap_between_inventory_and_open_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition

    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("aaa123DEF45", "Swapped cache", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )
    paths.transcripts.mkdir(parents=True)
    vtt = paths.transcripts / "20260820 - Swapped cache [aaa123DEF45].fr.vtt"
    vtt.write_text("WEBVTT\noriginal\n", encoding="utf-8")
    outside = tmp_path / "outside.vtt"
    outside.write_text("WEBVTT\nexternal\n", encoding="utf-8")
    original_list = acquisition._list_regular_names
    swapped = False

    def swapping_list(*args: object, **kwargs: object) -> tuple[str, ...]:
        nonlocal swapped
        names = original_list(*args, **kwargs)
        if not swapped and vtt.name in names:
            swapped = True
            vtt.rename(vtt.with_suffix(".original"))
            vtt.symlink_to(outside)
        return names

    monkeypatch.setattr(acquisition, "_list_regular_names", swapping_list)
    report = execute_acquisition(plan, refresh_indexes=False)

    assert report.exit_code == 1
    assert report.transcripts_ready == 0
    assert "changed" in report.failures[0] or "unsafe" in report.failures[0]


def test_analyzer_uses_private_snapshot_and_never_writes_swapped_destination(
    tmp_path: Path
) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    video = VideoInfo("aaa123DEF45", "Snapshot", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, analyze=True, discovered=[video]
    )
    paths.transcripts.mkdir(parents=True)
    paths.insights.mkdir()
    vtt = paths.transcripts / "20260820 - Snapshot [aaa123DEF45].fr.vtt"
    vtt.write_text("WEBVTT\ntrusted snapshot\n", encoding="utf-8")
    outside = tmp_path / "outside-insights"
    outside.mkdir()
    original_insights = paths.root / "original-insights"

    class Backend:
        def close(self) -> None:
            pass

    def fake_analyze(
        inputs: list[Path], output: Path, backend: object, config: object
    ) -> list[object]:
        assert paths.root not in inputs[0].parents
        assert inputs[0].read_bytes() == b"WEBVTT\ntrusted snapshot\n"
        paths.insights.rename(original_insights)
        paths.insights.symlink_to(outside, target_is_directory=True)
        (output / f"{inputs[0].stem}.json").write_text("{}", encoding="utf-8")
        return []

    report = execute_acquisition(
        plan,
        analyze_many=fake_analyze,
        backend_resolver=lambda config: Backend(),
        refresh_indexes=False,
    )

    assert report.exit_code == 4
    assert not any(outside.iterdir())
    assert (original_insights / f"{vtt.stem}.json").is_file()


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


def test_execute_preserves_downloader_error_when_vtt_was_written(tmp_path: Path) -> None:
    video = VideoInfo("aaa123DEF45", "Partial", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url,
        data_paths=DataPaths.from_root(tmp_path / "corpus"),
        discovered=[video],
    )

    def partial_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        output_dir.mkdir(parents=True)
        vtt = output_dir / "20260820 - Partial [aaa123DEF45].fr.vtt"
        vtt.write_text("WEBVTT\n", encoding="utf-8")
        return DownloadResult(
            vtt_files=[vtt], errors=["ERROR: metadata incomplete"], returncode=1
        )

    report = execute_acquisition(plan, download=partial_download, refresh_indexes=False)

    assert report.transcripts_ready == 1
    assert report.exit_code == 4
    assert report.failures == (
        "aaa123DEF45 (Partial): ERROR: metadata incomplete; "
        "yt-dlp exited with status 1",
    )


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


def test_execute_rebuilds_full_index_beyond_default_fifty(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths = DataPaths.from_root(tmp_path / "corpus")
    nested = paths.root / "bulk" / "transcripts"
    nested.mkdir(parents=True)
    for number in range(51):
        video_id = f"bulk{number:07d}"
        (nested / f"20260820 - Bulk {number} [{video_id}].fr.vtt").write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nEvidence\n",
            encoding="utf-8",
        )

    selected = VideoInfo("aaa123DEF45", "Selected", "20260820")
    plan = build_acquisition_plan(
        source=selected.watch_url,
        data_paths=paths,
        discovered=[selected],
    )

    def fake_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        output_dir.mkdir(parents=True)
        vtt = output_dir / "20260820 - Selected [aaa123DEF45].fr.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nSelected evidence\n",
            encoding="utf-8",
        )
        info = {"id": selected.video_id, "channel_id": "UCSelected", "channel": "Selected"}
        (output_dir / "20260820 - Selected [aaa123DEF45].info.json").write_text(
            json.dumps(info), encoding="utf-8"
        )
        return DownloadResult(vtt_files=[vtt])

    report = execute_acquisition(plan, download=fake_download)

    assert report.exit_code == 0
    assert SQLiteFtsIndex(paths.search_database).status().documents_indexed == 52


def test_execute_refreshes_private_staging_and_preserves_discovery_only_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    from yt_insights.catalog import Catalog
    from yt_insights.downloader import VideoListResult

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.root.mkdir(parents=True)
    discovered = VideoInfo("disc123ABCD", "Discovery only", "20260819")
    with Catalog(paths.catalog_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@stable/videos",
            VideoListResult(videos=[discovered]),
        )

    selected = VideoInfo("aaa123DEF45", "Imported", "20260820")
    plan = build_acquisition_plan(
        source=selected.watch_url,
        data_paths=paths,
        discovered=[selected],
    )

    def fake_download(source: str, output_dir: Path, **_: object) -> DownloadResult:
        output_dir.mkdir(parents=True)
        vtt = output_dir / "20260820 - Imported [aaa123DEF45].fr.vtt"
        vtt.write_text("WEBVTT\nEvidence\n", encoding="utf-8")
        return DownloadResult(vtt_files=[vtt])

    original_connect = sqlite3.connect

    def guarded_connect(database: object, *args: object, **kwargs: object):
        if not kwargs.get("uri") and Path(database) in {
            paths.catalog_database,
            paths.search_database,
        }:
            raise AssertionError("public database path was opened")
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    report = execute_acquisition(plan, download=fake_download)

    assert report.exit_code == 0
    with original_connect(paths.catalog_database) as connection:
        assert connection.execute(
            "SELECT title FROM videos WHERE video_id = ?", (discovered.video_id,)
        ).fetchone() == ("Discovery only",)
        assert connection.execute(
            "SELECT title FROM videos WHERE video_id = ?", (selected.video_id,)
        ).fetchone() == ("Imported",)


def test_execute_refuses_nonempty_catalog_wal_without_replacing_catalog(
    tmp_path: Path,
) -> None:
    from yt_insights.catalog import Catalog

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.root.mkdir(parents=True)
    with Catalog(paths.catalog_database):
        pass
    original = paths.catalog_database.read_bytes()
    paths.catalog_database.with_name(paths.catalog_database.name + "-wal").write_bytes(
        b"uncheckpointed"
    )
    paths.transcripts.mkdir()
    cached = paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt"
    cached.write_text("WEBVTT\n", encoding="utf-8")
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("non-empty catalog WAL" in failure for failure in report.failures)
    assert paths.catalog_database.read_bytes() == original


def test_execute_rechecks_catalog_wal_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.catalog import Catalog

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.root.mkdir(parents=True)
    with Catalog(paths.catalog_database):
        pass
    original = paths.catalog_database.read_bytes()
    wal = paths.catalog_database.with_name(paths.catalog_database.name + "-wal")
    original_import = Catalog.import_corpus

    def racing_import(self: Catalog, root: Path):
        summary = original_import(self, root)
        wal.write_bytes(b"concurrent frames")
        return summary

    monkeypatch.setattr(Catalog, "import_corpus", racing_import)
    paths.transcripts.mkdir()
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("non-empty catalog WAL" in failure for failure in report.failures)
    assert paths.catalog_database.read_bytes() == original


def test_execute_replaces_raced_catalog_symlink_without_writing_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.catalog import Catalog

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.root.mkdir(parents=True)
    with Catalog(paths.catalog_database):
        pass
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"outside sentinel")
    original_import = Catalog.import_corpus
    swapped = False

    def swapping_import(self: Catalog, root: Path):
        nonlocal swapped
        if not swapped:
            swapped = True
            paths.catalog_database.rename(paths.root / "raced-catalog.sqlite3")
            paths.catalog_database.symlink_to(outside)
        return original_import(self, root)

    monkeypatch.setattr(Catalog, "import_corpus", swapping_import)
    paths.transcripts.mkdir()
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )

    report = execute_acquisition(plan)

    assert report.exit_code == 0
    assert paths.catalog_database.is_file()
    assert not paths.catalog_database.is_symlink()
    assert outside.read_bytes() == b"outside sentinel"


def test_execute_refuses_search_parent_swap_without_writing_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    paths.search_database.parent.mkdir()
    outside = tmp_path / "outside-search"
    outside.mkdir()
    moved = paths.root / "original-search"
    original_rebuild = SQLiteFtsIndex.rebuild

    def swapping_rebuild(self: SQLiteFtsIndex, manifest: object):
        report = original_rebuild(self, manifest)
        paths.search_database.parent.rename(moved)
        paths.search_database.parent.symlink_to(outside, target_is_directory=True)
        return report

    monkeypatch.setattr(SQLiteFtsIndex, "rebuild", swapping_rebuild)
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("index:" in failure for failure in report.failures)
    assert not any(outside.iterdir())


def test_execute_refuses_preexisting_search_receipt_without_replacing_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.search.sqlite_fts as sqlite_fts

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    paths.search_database.parent.mkdir()
    paths.search_database.write_bytes(b"published sentinel")
    generation_id = "0" * 32
    receipt = paths.search_database.with_name(
        f".{paths.search_database.name}.{generation_id}.receipt.json"
    )
    receipt.write_text('{"falsified":true}', encoding="utf-8")
    monkeypatch.setattr(sqlite_fts.secrets, "token_hex", lambda _: generation_id)
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("publication target already exists" in item for item in report.failures)
    assert paths.search_database.read_bytes() == b"published sentinel"
    assert receipt.read_text(encoding="utf-8") == '{"falsified":true}'


def test_execute_holds_catalog_lock_through_import_and_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition_module
    from yt_insights.catalog import Catalog

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    lock_path = paths.root / ".catalog.sqlite3.lock"
    lock_path.touch()
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )
    checkpoints: list[str] = []
    original_import = Catalog.import_corpus
    original_replace = acquisition_module._replace_regular_file

    def checked_import(self: Catalog, root: Path):
        checkpoints.append("import")
        _assert_catalog_lock_held(lock_path)
        return original_import(self, root)

    def checked_replace(
        source: Path, destination_fd: int, name: str, *args: object, **kwargs: object
    ) -> None:
        if name == paths.catalog_database.name:
            checkpoints.append("publish")
            _assert_catalog_lock_held(lock_path)
        original_replace(source, destination_fd, name, *args, **kwargs)

    monkeypatch.setattr(Catalog, "import_corpus", checked_import)
    monkeypatch.setattr(acquisition_module, "_replace_regular_file", checked_replace)

    report = execute_acquisition(plan)

    assert report.exit_code == 0
    assert checkpoints == ["import", "publish"]


def test_execute_restores_old_catalog_when_post_publication_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    import yt_insights.acquisition as acquisition_module
    from yt_insights.catalog import Catalog
    from yt_insights.downloader import VideoListResult

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.root.mkdir(parents=True)
    discovered = VideoInfo("disc123ABCD", "Discovery survives", "20260819")
    with Catalog(paths.catalog_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@stable/videos",
            VideoListResult(videos=[discovered]),
        )
        catalog.checkpoint()
    paths.transcripts.mkdir()
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )
    original_replace = acquisition_module._replace_regular_file
    corrupted = False

    def corrupting_replace(
        source: Path, destination_fd: int, name: str, *args: object, **kwargs: object
    ) -> None:
        nonlocal corrupted
        original_replace(source, destination_fd, name, *args, **kwargs)
        if name == paths.catalog_database.name and not corrupted:
            corrupted = True
            paths.catalog_database.write_bytes(b"corrupted catalog")

    monkeypatch.setattr(acquisition_module, "_replace_regular_file", corrupting_replace)

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("catalog" in failure for failure in report.failures)
    with sqlite3.connect(
        f"{paths.catalog_database.absolute().as_uri()}?mode=ro", uri=True
    ) as connection:
        assert connection.execute(
            "SELECT title FROM videos WHERE video_id = ?", (discovered.video_id,)
        ).fetchone() == ("Discovery survives",)


def _valid_search_pair(tmp_path: Path) -> tuple[DataPaths, AcquisitionPlan, object, set[str]]:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - Cached [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\n", encoding="utf-8"
    )
    video = VideoInfo("aaa123DEF45", "Cached", "20260820")
    plan = build_acquisition_plan(
        source=video.watch_url, data_paths=paths, discovered=[video]
    )
    assert execute_acquisition(plan).exit_code == 0
    status = SQLiteFtsIndex(paths.search_database).status()
    receipts = {
        path.name
        for path in paths.search_database.parent.glob(
            f".{paths.search_database.name}.*.receipt.json"
        )
    }
    return paths, plan, status, receipts


def _corrupt_new_receipt(paths: DataPaths, old_receipts: set[str]) -> None:
    receipts = [
        path
        for path in paths.search_database.parent.glob(
            f".{paths.search_database.name}.*.receipt.json"
        )
        if path.name not in old_receipts
    ]
    assert len(receipts) == 1
    receipts[0].write_bytes(b'{"corrupted":true}')


def test_execute_restores_old_search_pair_when_receipt_changes_before_db_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition_module
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths, plan, old_status, old_receipts = _valid_search_pair(tmp_path)
    original_replace = acquisition_module._replace_regular_file

    def corrupting_replace(
        source: Path, destination_fd: int, name: str, *args: object, **kwargs: object
    ) -> None:
        if name == paths.search_database.name:
            _corrupt_new_receipt(paths, old_receipts)
        original_replace(source, destination_fd, name, *args, **kwargs)

    monkeypatch.setattr(acquisition_module, "_replace_regular_file", corrupting_replace)

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("receipt" in failure for failure in report.failures)
    assert SQLiteFtsIndex(paths.search_database).status() == old_status


def test_execute_restores_old_search_pair_when_receipt_changes_after_db_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition_module
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths, plan, old_status, old_receipts = _valid_search_pair(tmp_path)
    original_replace = acquisition_module._replace_regular_file

    def corrupting_replace(
        source: Path, destination_fd: int, name: str, *args: object, **kwargs: object
    ) -> None:
        original_replace(source, destination_fd, name, *args, **kwargs)
        if name == paths.search_database.name:
            _corrupt_new_receipt(paths, old_receipts)

    monkeypatch.setattr(acquisition_module, "_replace_regular_file", corrupting_replace)

    report = execute_acquisition(plan)

    assert report.exit_code == 4
    assert any("receipt" in failure for failure in report.failures)
    assert SQLiteFtsIndex(paths.search_database).status() == old_status


def test_refresh_restores_both_previous_databases_when_search_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition
    from yt_insights.catalog import Catalog
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    paths, _, old_search_status, old_receipts = _valid_search_pair(tmp_path)
    old_catalog = paths.catalog_database.read_bytes()
    old_search = paths.search_database.read_bytes()
    extra = paths.transcripts / "20260821 - New [new123ABCDE].fr.vtt"
    extra.write_text("WEBVTT\nNew evidence\n", encoding="utf-8")

    monkeypatch.setattr(
        acquisition,
        "_validate_published_search_pair",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("forced failure")),
    )

    with pytest.raises(
        ValueError, match=r"search.*publication validation failed"
    ):
        acquisition.rebuild_and_publish_indexes(paths)

    assert paths.catalog_database.read_bytes() == old_catalog
    assert paths.search_database.read_bytes() == old_search
    Catalog.validate_database(paths.catalog_database)
    assert SQLiteFtsIndex(paths.search_database).status() == old_search_status
    assert {
        path.name
        for path in paths.search_database.parent.glob(
            f".{paths.search_database.name}.*.receipt.json"
        )
    } == old_receipts


def test_refresh_removes_both_new_databases_when_no_previous_pair_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - New [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\nNew evidence\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        acquisition,
        "_validate_published_search_pair",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("forced failure")),
    )

    with pytest.raises(
        ValueError, match=r"search.*publication validation failed"
    ):
        acquisition.rebuild_and_publish_indexes(paths)

    assert not paths.catalog_database.exists()
    assert not paths.search_database.exists()
    assert not tuple(
        paths.search_database.parent.glob(
            f".{paths.search_database.name}.*.receipt.json"
        )
    )


def test_public_refresh_returns_validated_pair_report(tmp_path: Path) -> None:
    import yt_insights.acquisition as acquisition

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - New [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\nNew evidence\n", encoding="utf-8"
    )

    assert acquisition.rebuild_and_publish_indexes(paths) == (
        acquisition.IndexRefreshReport(
            catalog_published=True,
            search_published=True,
        )
    )


def test_refresh_removes_new_catalog_when_its_public_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.acquisition as acquisition
    from yt_insights.catalog import Catalog

    paths = DataPaths.from_root(tmp_path / "corpus")
    paths.transcripts.mkdir(parents=True)
    (paths.transcripts / "20260820 - New [aaa123DEF45].fr.vtt").write_text(
        "WEBVTT\nNew evidence\n", encoding="utf-8"
    )
    original_validate = Catalog.validate_database

    def fail_published_catalog(database_path: Path) -> None:
        if database_path.parent.name == "published-catalog":
            raise ValueError("forced catalogue validation failure")
        original_validate(database_path)

    monkeypatch.setattr(
        Catalog, "validate_database", staticmethod(fail_published_catalog)
    )

    with pytest.raises(ValueError, match="catalog post-publication validation failed"):
        acquisition.rebuild_and_publish_indexes(paths)

    assert not paths.catalog_database.exists()
    assert not paths.search_database.exists()


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
