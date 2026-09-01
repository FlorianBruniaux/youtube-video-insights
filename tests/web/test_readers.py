from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

import yt_insights.web.readers as readers_module
from yt_insights.catalog import Catalog
from yt_insights.downloader import VideoInfo, VideoListResult
from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import (
    BuildReport,
    DocumentRef,
    Passage,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)
from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex
from yt_insights.web.readers import (
    CatalogWebReader,
    ExportReader,
    SearchIndexWebReader,
)

VIDEO_ID = "abc123DEF45"


def test_search_index_reader_returns_counts_and_exact_video_membership(
    tmp_path: Path,
) -> None:
    """Inferring index state from transcript presence would report stale indexes as ready."""
    document_id = compute_document_id("channel-a", VIDEO_ID, "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath=f"channel-a/transcripts/Local [{VIDEO_ID}].en.vtt",
        source_sha256="a" * 64,
        channel_id="channel-a",
        channel_title="Channel A",
        video_id=VIDEO_ID,
        video_title="Local model",
        language="en",
    )
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 1.0, 3.0, "Local evidence"),
        document_id=document_id,
        ordinal=0,
        start_seconds=1.0,
        end_seconds=3.0,
        text="Local evidence",
        youtube_url=youtube_url(VIDEO_ID, 1.0),
    )
    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(CorpusManifest((document,), (passage,), (), 1, 1, 0))
    reader = SearchIndexWebReader(index)

    assert reader.status().documents_indexed == 1
    assert reader.indexed_video_ids((VIDEO_ID, "missingVid1")) == frozenset(
        {VIDEO_ID}
    )


def test_search_index_reader_translates_membership_storage_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal SQLite failures must retain the fixed public index mapping."""
    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    reader = SearchIndexWebReader(index)
    monkeypatch.setattr(index, "status", lambda: BuildReport(0, 0, 0, 0, 0))

    def fail_open() -> object:
        raise sqlite3.OperationalError("private index detail")

    monkeypatch.setattr(index, "_open_active_readonly", fail_open)

    with pytest.raises(SearchIndexError, match="membership query failed"):
        reader.indexed_video_ids((VIDEO_ID,))


def _write_transcript(root: Path, *, language: str) -> None:
    directory = root / "example" / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"20260820 - Local model [{VIDEO_ID}].{language}.vtt"
    (directory / stem).write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nLocal evidence\n",
        encoding="utf-8",
    )


def _catalog(root: Path) -> Path:
    database = root / "catalog.sqlite3"
    _write_transcript(root, language="en")
    _write_transcript(root, language="fr")
    with Catalog(database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@example/videos",
            VideoListResult(
                videos=[VideoInfo(VIDEO_ID, "Local model", "20260828")],
                errors=[],
                returncode=0,
            ),
        )
        catalog.import_corpus(root)
        catalog.checkpoint()
    return database


def _manifest(*, session_id: str = "s" * 32) -> dict[str, object]:
    return {
        "acquisition_outcomes": [],
        "assessment": None,
        "candidates": [],
        "coverage_limits": [],
        "decisions": [],
        "dossier_sha256": "a" * 64,
        "evidence": [],
        "format_version": 1,
        "package_version": "0.2.0",
        "session": {
            "created_at": "2026-08-31T10:00:00+00:00",
            "session_id": session_id,
        },
    }


def test_catalog_reader_projects_only_bounded_public_source_fields(tmp_path: Path) -> None:
    """Leaking database columns, paths, or unchecked links breaks this projection."""
    class IndexReader:
        def status(self) -> BuildReport:
            return BuildReport(2, 1, 1, 1, 3)

        def indexed_video_ids(self, video_ids: tuple[str, ...]) -> frozenset[str]:
            assert video_ids == (VIDEO_ID,)
            return frozenset({VIDEO_ID})

    reader = CatalogWebReader(_catalog(tmp_path), search_index=IndexReader())

    payload = reader.list_sources(limit=1, offset=0)

    assert payload == {
        "items": [
            {
                "video_id": VIDEO_ID,
                "title": "Local model",
                "published_at": "2026-08-20",
                "languages": ["en", "fr"],
                "sources": ["example"],
                "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                "artifact_count": 2,
                "transcript_state": "available",
                "index_state": "indexed",
            }
        ],
        "limit": 1,
        "offset": 0,
    }
    items = payload["items"]
    assert isinstance(items, list)
    assert isinstance(items[0], dict)
    assert set(items[0]) == {
        "video_id",
        "title",
        "published_at",
        "languages",
        "sources",
        "url",
        "artifact_count",
        "transcript_state",
        "index_state",
    }

    assert reader.corpus_status() == {
        "health": "ready",
        "videos": 1,
        "transcripts": 2,
        "documents_indexed": 1,
        "passages_indexed": 3,
    }


def test_export_reader_reads_nested_dossiers_and_hides_unsafe_entries(
    tmp_path: Path,
) -> None:
    """Flattening real dossiers, following links, or returning paths crosses the boundary."""
    exports = tmp_path / "exports"
    valid = exports / "local-ai" / ("2026-08-31-" + "s" * 32)
    malformed = exports / "unsafe" / ("2026-08-31-" + "x" * 32)
    outside = tmp_path / "outside"
    for directory in (valid, malformed, outside / "2026-08-31-outside"):
        directory.mkdir(parents=True)
    dossier = b"# Local AI dossier\n"
    valid_manifest = _manifest()
    valid_manifest["dossier_sha256"] = hashlib.sha256(dossier).hexdigest()
    (valid / "manifest.json").write_text(json.dumps(valid_manifest), encoding="utf-8")
    (valid / "dossier.md").write_bytes(dossier)
    (outside / "2026-08-31-outside" / "manifest.json").write_text(
        json.dumps(_manifest()),
        encoding="utf-8",
    )
    (malformed / "manifest.json").write_text(
        json.dumps(_manifest(session_id="/private/secret-session")),
        encoding="utf-8",
    )
    os.symlink(outside, exports / "linked-topic")
    os.symlink(outside, exports / "local-ai" / "linked-dossier")

    reader = ExportReader(exports)
    payload = reader.list_exports(limit=10)
    export_id = hashlib.sha256(
        ("local-ai\x00" + valid.name).encode("utf-8")
    ).hexdigest()

    assert payload == {
        "items": [
            {
                "name": "2026-08-31-" + "s" * 32,
                "session_id": "s" * 32,
                "created_at": "2026-08-31T10:00:00+00:00",
                "manifest_valid": True,
                "export_id": export_id,
                "open_url": f"/api/v1/exports/{export_id}/dossier",
            },
            {
                "name": "2026-08-31-" + "x" * 32,
                "session_id": None,
                "created_at": None,
                "manifest_valid": False,
                "export_id": hashlib.sha256(
                    ("unsafe\x00" + malformed.name).encode("utf-8")
                ).hexdigest(),
                "open_url": None,
            },
        ],
        "limit": 10,
        "truncated": False,
        "inventory_complete": True,
        "inventory_examined": 6,
        "inventory_limit": 32,
    }
    assert "linked-topic" not in repr(payload)
    assert "linked-dossier" not in repr(payload)
    assert "/private/secret-session" not in repr(payload)
    items = payload["items"]
    assert isinstance(items, list)
    assert all(
        isinstance(item, dict)
        and set(item)
        == {
            "name",
            "session_id",
            "created_at",
            "manifest_valid",
            "export_id",
            "open_url",
        }
        for item in items
    )
    assert reader.read_dossier(export_id) == dossier
    assert reader.read_dossier("b" * 64) is None


def test_export_reader_caps_descriptor_inventory_without_listdir_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scanning every hostile export child must not happen after a small page fills."""
    exports = tmp_path / "exports"
    for index in range(33):
        (exports / f"topic-{index:02d}").mkdir(parents=True)

    def fail_listdir(_descriptor: int) -> list[str]:
        raise AssertionError("export reader must not materialize directory entries")

    monkeypatch.setattr(os, "listdir", fail_listdir)

    assert ExportReader(exports).list_exports(limit=1) == {
        "items": [],
        "limit": 1,
        "truncated": True,
        "inventory_complete": False,
        "inventory_examined": 32,
        "inventory_limit": 32,
    }


def test_export_reader_discloses_a_valid_dossier_beyond_its_inventory_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A capped scan must disclose that a valid unexamined dossier may exist."""
    exports = tmp_path / "exports"
    for index in range(32):
        (exports / f"topic-{index:02d}").mkdir(parents=True)
    hidden = exports / "topic-32" / "2026-08-31-hidden"
    hidden.mkdir(parents=True)
    (hidden / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    original_scandir = os.scandir

    class SortedScandir:
        def __init__(self, descriptor: int) -> None:
            with original_scandir(descriptor) as entries:
                self._entries = iter(sorted(entries, key=lambda entry: entry.name))

        def __enter__(self) -> object:
            return self._entries

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(readers_module.os, "scandir", SortedScandir)

    assert ExportReader(exports).list_exports(limit=10) == {
        "items": [],
        "limit": 10,
        "truncated": True,
        "inventory_complete": False,
        "inventory_examined": 32,
        "inventory_limit": 32,
    }


def test_export_reader_stably_sorts_examined_dossiers(tmp_path: Path) -> None:
    """Directory iteration order must not change the public order of examined dossiers."""
    exports = tmp_path / "exports" / "topic"
    first = exports / "2026-08-31-z"
    second = exports / "2026-08-31-a"
    for directory, session_id in ((first, "z" * 32), (second, "a" * 32)):
        directory.mkdir(parents=True)
        (directory / "manifest.json").write_text(
            json.dumps(_manifest(session_id=session_id)), encoding="utf-8"
        )

    payload = ExportReader(exports.parent).list_exports(limit=10)

    items = payload["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    assert [item["name"] for item in items] == [
        "2026-08-31-a",
        "2026-08-31-z",
    ]
    assert payload["inventory_complete"] is True


@pytest.mark.parametrize(
    ("blocked_name", "inventory_examined"),
    (("topic", 1), ("2026-08-31-dossier", 2)),
)
def test_export_reader_marks_unopenable_candidate_directories_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked_name: str,
    inventory_examined: int,
) -> None:
    """A candidate lost between scan and no-follow open must not look complete."""
    exports = tmp_path / "exports"
    dossier = exports / "topic" / "2026-08-31-dossier"
    dossier.mkdir(parents=True)
    (dossier / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    original_open = os.open

    def denied_open(
        name: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if name == blocked_name and dir_fd is not None:
            raise PermissionError("test candidate became unavailable")
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(readers_module.os, "open", denied_open)

    assert ExportReader(exports).list_exports(limit=10) == {
        "items": [],
        "limit": 10,
        "truncated": True,
        "inventory_complete": False,
        "inventory_examined": inventory_examined,
        "inventory_limit": 32,
    }


def test_export_reader_marks_unreadable_manifest_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treating an I/O race as a malformed manifest would overstate completeness."""
    exports = tmp_path / "exports"
    dossier = exports / "topic" / "2026-08-31-dossier"
    dossier.mkdir(parents=True)
    (dossier / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    original_open = os.open

    def denied_open(
        name: str | Path,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if name == "manifest.json" and dir_fd is not None:
            raise PermissionError("test manifest became unreadable")
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(readers_module.os, "open", denied_open)

    payload = ExportReader(exports).list_exports(limit=10)

    assert payload["inventory_complete"] is False
    assert payload["truncated"] is True


def test_export_reader_manifest_regular_file_to_fifo_race_is_nonblocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest replaced by a FIFO after stat must not block serialized reads."""
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking or not hasattr(os, "mkfifo"):
        pytest.skip("nonblocking FIFOs are unavailable")
    exports = tmp_path / "exports"
    dossier = exports / "topic" / "2026-08-31-dossier"
    dossier.mkdir(parents=True)
    manifest = dossier / "manifest.json"
    manifest.write_text(json.dumps(_manifest()), encoding="utf-8")
    original_stat = os.stat
    original_open = os.open
    raced = False

    def racing_stat(
        name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal raced
        details = original_stat(name, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if name == "manifest.json" and dir_fd is not None and not raced:
            raced = True
            manifest.unlink()
            os.mkfifo(manifest)
        return details

    def guarded_open(
        name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if name == "manifest.json" and dir_fd is not None:
            assert flags & nonblocking
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(readers_module.os, "stat", racing_stat)
    monkeypatch.setattr(readers_module.os, "open", guarded_open)

    payload = ExportReader(exports).list_exports(limit=10)

    assert raced is True
    assert payload["inventory_complete"] is False
    assert payload["truncated"] is True


def test_export_reader_dossier_fifo_is_rejected_without_blocking(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening a dossier FIFO without O_NONBLOCK would hang reads and shutdown."""
    nonblocking = getattr(os, "O_NONBLOCK", 0)
    if not nonblocking or not hasattr(os, "mkfifo"):
        pytest.skip("nonblocking FIFOs are unavailable")
    exports = tmp_path / "exports"
    dossier = exports / "topic" / "2026-08-31-dossier"
    dossier.mkdir(parents=True)
    (dossier / "manifest.json").write_text(
        json.dumps(_manifest()), encoding="utf-8"
    )
    os.mkfifo(dossier / "dossier.md")
    original_open = os.open

    def guarded_open(
        name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if name == "dossier.md" and dir_fd is not None:
            assert flags & nonblocking
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(readers_module.os, "open", guarded_open)
    reader = ExportReader(exports)
    item = reader.list_exports(limit=10)["items"][0]
    assert isinstance(item, dict)

    assert reader.read_dossier(str(item["export_id"])) is None
