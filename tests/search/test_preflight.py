from __future__ import annotations

import os
from collections import namedtuple
from pathlib import Path

import pytest

DiskUsage = namedtuple("DiskUsage", "total used free")


def test_preflight_counts_in_root_vtts_and_excludes_escaping_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import preflight

    root = tmp_path / "output"
    included = root / "alpha" / "transcripts" / "included.vtt"
    included.parent.mkdir(parents=True)
    included.write_bytes(b"included")
    outside = tmp_path / "outside.vtt"
    outside.write_bytes(b"outside")
    (root / "alpha" / "transcripts" / "outside-link.vtt").symlink_to(outside)
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda _: DiskUsage(1, 1, 512 * 1024**2))

    report = preflight.preflight_index_space(root, tmp_path / "search.db")

    assert report.sources_discovered == 2
    assert report.source_files == 1
    assert report.sources_excluded == 1
    assert report.source_bytes == len(b"included")
    assert report.required_bytes == 256 * 1024**2
    assert report.available_bytes == 512 * 1024**2


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO support is unavailable")
def test_preflight_excludes_non_regular_sources_and_non_transcript_vtts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import preflight

    root = tmp_path / "output"
    included = root / "alpha" / "transcripts" / "included.vtt"
    included.parent.mkdir(parents=True)
    included.write_bytes(b"included")
    (root / "ignored.vtt").write_bytes(b"not a transcript candidate")
    (root / "alpha" / "transcripts" / "directory.vtt").mkdir()
    fifo = root / "alpha" / "transcripts" / "source.vtt"
    os.mkfifo(fifo)
    (root / "alpha" / "transcripts" / "source-link.vtt").symlink_to(fifo)
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda _: DiskUsage(1, 1, 512 * 1024**2))

    report = preflight.preflight_index_space(root, tmp_path / "search.db")

    assert report.sources_discovered == 4
    assert report.source_files == 1
    assert report.sources_excluded == 3
    assert report.source_bytes == len(b"included")


@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
def test_preflight_fails_closed_when_a_regular_source_cannot_be_reinventoried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[OSError]
) -> None:
    from yt_insights.search import preflight

    root = tmp_path / "output"
    source = root / "alpha" / "transcripts" / "source.vtt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")

    def raise_during_inventory(*_: object) -> int:
        raise failure("source unavailable")

    monkeypatch.setattr(preflight, "_stable_file_size", raise_during_inventory)

    with pytest.raises(preflight.IndexSpacePreflightError, match=r"source\.vtt"):
        preflight.preflight_index_space(root, tmp_path / "search.db")


def test_preflight_uses_the_nearest_existing_database_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import preflight

    root = tmp_path / "output"
    root.mkdir()
    disk_usage_paths: list[Path] = []
    monkeypatch.setattr(
        preflight.shutil,
        "disk_usage",
        lambda path: disk_usage_paths.append(Path(path)) or DiskUsage(1, 1, 512 * 1024**2),
    )

    report = preflight.preflight_index_space(root, tmp_path / "missing" / "nested" / "search.db")

    assert report.disk_usage_path == tmp_path
    assert disk_usage_paths == [tmp_path]


def test_preflight_rejects_a_missing_corpus_root(tmp_path: Path) -> None:
    from yt_insights.search.preflight import preflight_index_space

    with pytest.raises(ValueError, match="corpus_root"):
        preflight_index_space(tmp_path / "missing", tmp_path / "search.db")


def test_preflight_raises_an_actionable_domain_error_when_space_is_insufficient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import preflight

    root = tmp_path / "output"
    source = root / "alpha" / "transcripts" / "source.vtt"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x")
    monkeypatch.setattr(preflight.shutil, "disk_usage", lambda _: DiskUsage(1, 1, 1))

    with pytest.raises(
        preflight.InsufficientIndexSpace, match=r"available.*required"
    ):
        preflight.preflight_index_space(root, tmp_path / "search.db")
