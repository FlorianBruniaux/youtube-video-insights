from __future__ import annotations

from pathlib import Path

from yt_insights.paths import DataPaths


def test_data_paths_derive_every_database_and_artifact_directory(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")

    assert paths.root == tmp_path / "corpus"
    assert paths.transcripts == tmp_path / "corpus" / "transcripts"
    assert paths.insights == tmp_path / "corpus" / "insights"
    assert paths.shorts == tmp_path / "corpus" / "shorts"
    assert paths.clips == tmp_path / "corpus" / "clips"
    assert paths.exports == tmp_path / "corpus" / "exports"
    assert paths.catalog_database == tmp_path / "corpus" / "catalog.sqlite3"
    assert paths.search_database == tmp_path / "corpus" / ".search" / "search-v1.sqlite3"


def test_data_paths_resolve_roots_without_using_the_current_directory(tmp_path: Path, monkeypatch) -> None:
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)

    paths = DataPaths.from_root(tmp_path / "corpus")

    assert paths.root == tmp_path / "corpus"
