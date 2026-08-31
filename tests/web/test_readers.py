from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import yt_insights.web.readers as readers_module
from yt_insights.catalog import Catalog
from yt_insights.downloader import VideoInfo, VideoListResult
from yt_insights.web.readers import CatalogWebReader, ExportReader

VIDEO_ID = "abc123DEF45"


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
    reader = CatalogWebReader(_catalog(tmp_path))

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
    (valid / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
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

    payload = ExportReader(exports).list_exports(limit=10)

    assert payload == {
        "items": [
            {
                "name": "2026-08-31-" + "s" * 32,
                "session_id": "s" * 32,
                "created_at": "2026-08-31T10:00:00+00:00",
                "manifest_valid": True,
            },
            {
                "name": "2026-08-31-" + "x" * 32,
                "session_id": None,
                "created_at": None,
                "manifest_valid": False,
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
        and set(item) == {"name", "session_id", "created_at", "manifest_valid"}
        for item in items
    )


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
