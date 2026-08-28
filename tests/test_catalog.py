from __future__ import annotations

import json
import fcntl
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from yt_insights.catalog import Catalog, CatalogError
from yt_insights.cleaner import clean_vtt
from yt_insights.downloader import VideoInfo, VideoListResult


VIDEO_ID = "abc123DEF45"


def _write_video_artifacts(
    root: Path,
    *,
    channel: str,
    language: str,
    transcript: str,
    video_id: str = VIDEO_ID,
) -> None:
    stem = f"20260820 - Agentic product discovery [{video_id}].{language}"
    transcripts = root / channel / "transcripts"
    insights = root / channel / "insights"
    transcripts.mkdir(parents=True, exist_ok=True)
    insights.mkdir(parents=True, exist_ok=True)

    (transcripts / f"{stem}.vtt").write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n" + transcript + "\n",
        encoding="utf-8",
    )
    (insights / f"{stem}.json").write_text(
        json.dumps(
            {
                "subject": f"Product discovery with AI ({language})",
                "key_points": ["Interview users before automating"],
                "tools": [{"name": "Claude", "context": "Research synthesis"}],
                "advice": ["Keep the evidence linked to the source"],
                "quotes": [],
            }
        ),
        encoding="utf-8",
    )


class CatalogImportTests(unittest.TestCase):
    def test_import_never_follows_symlinked_layout_or_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            outside = base / "outside"
            outside_transcripts = outside / "transcripts"
            outside_transcripts.mkdir(parents=True)
            corpus.mkdir()
            filename = f"20260820 - Outside [{VIDEO_ID}].fr.vtt"
            outside_vtt = outside_transcripts / filename
            outside_vtt.write_text("WEBVTT\n", encoding="utf-8")
            (corpus / "escape").symlink_to(outside, target_is_directory=True)
            flat = corpus / "transcripts"
            flat.mkdir()
            (flat / filename).symlink_to(outside_vtt)

            with Catalog(base / "catalog.sqlite3") as catalog:
                summary = catalog.import_corpus(corpus)
                stats = catalog.stats()

            self.assertEqual(summary.items_seen, 0)
            self.assertEqual(stats.videos, 0)
            self.assertEqual(stats.artifacts, 0)

    def test_import_file_swap_between_inventory_and_open_never_indexes_external_bytes(
        self,
    ) -> None:
        import yt_insights.catalog as catalog_module

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            transcripts = corpus / "channel" / "transcripts"
            transcripts.mkdir(parents=True)
            filename = f"20260820 - Original [{VIDEO_ID}].fr.vtt"
            vtt = transcripts / filename
            vtt.write_text("WEBVTT\noriginal\n", encoding="utf-8")
            outside = base / "outside.vtt"
            outside.write_text("WEBVTT\nexternal secret\n", encoding="utf-8")
            original_list = catalog_module._list_regular_names
            swapped = False

            def swapping_list(*args: object, **kwargs: object) -> tuple[str, ...]:
                nonlocal swapped
                names = original_list(*args, **kwargs)
                if not swapped and filename in names:
                    swapped = True
                    vtt.rename(vtt.with_suffix(".original"))
                    vtt.symlink_to(outside)
                return names

            with patch.object(catalog_module, "_list_regular_names", swapping_list):
                with Catalog(base / "catalog.sqlite3") as catalog:
                    summary = catalog.import_corpus(corpus)
                    stats = catalog.stats()

            self.assertEqual(summary.items_seen, 0)
            self.assertEqual(stats.videos, 0)
            self.assertEqual(stats.artifacts, 0)

    def test_import_includes_flat_inbox_and_nested_corpus_without_double_counting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            nested = corpus / "product-channel" / "transcripts"
            flat = corpus / "transcripts"
            nested.mkdir(parents=True)
            flat.mkdir(parents=True)
            filename = f"20260820 - Agentic product discovery [{VIDEO_ID}].fr.vtt"
            payload = "WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nEvidence\n"
            (nested / filename).write_text(payload, encoding="utf-8")
            (flat / filename).write_text(payload, encoding="utf-8")

            with Catalog(base / "catalog.sqlite3") as catalog:
                summary = catalog.import_corpus(corpus)
                stats = catalog.stats()

            self.assertEqual(summary.items_seen, 2)
            self.assertEqual(summary.items_written, 1)
            self.assertEqual(stats.videos, 1)
            self.assertEqual(stats.artifacts, 1)

    def test_import_is_idempotent_across_language_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="fr",
                transcript="Une phrase française sur la veille produit.",
            )
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="An English sentence about product intelligence.",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                first = catalog.import_corpus(corpus)
                first_stats = catalog.stats()
                second = catalog.import_corpus(corpus)
                second_stats = catalog.stats()

            self.assertEqual(first.status, "completed")
            self.assertEqual(first.items_seen, 4)
            self.assertEqual(first.items_written, 4)
            self.assertEqual(first.error_count, 0)
            self.assertEqual(first_stats.videos, 1)
            self.assertEqual(first_stats.sources, 1)
            self.assertEqual(first_stats.artifacts, 4)
            self.assertEqual(first_stats.runs, 1)

            self.assertEqual(second.status, "completed")
            self.assertEqual(second.items_seen, 4)
            self.assertEqual(second.items_written, 0)
            self.assertEqual(second.error_count, 0)
            self.assertEqual(second_stats.videos, 1)
            self.assertEqual(second_stats.sources, 1)
            self.assertEqual(second_stats.artifacts, 4)
            self.assertEqual(second_stats.runs, 2)

    def test_noop_import_skips_vtt_cleaning_and_fts_reindex(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="A stable transcript that is already indexed.",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                catalog.import_corpus(corpus)
                with (
                    patch("yt_insights.catalog.clean_vtt", wraps=clean_vtt) as cleaner,
                    patch.object(
                        catalog,
                        "_reindex_video",
                        wraps=catalog._reindex_video,
                    ) as reindex,
                ):
                    second = catalog.import_corpus(corpus)

            self.assertEqual(second.items_written, 0)
            cleaner.assert_not_called()
            reindex.assert_not_called()

    def test_fatal_import_failure_rolls_back_domain_rows_and_marks_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="A valid transcript before an indexing failure.",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                with (
                    patch.object(
                        catalog,
                        "_reindex_video",
                        side_effect=RuntimeError("FTS indexing unavailable"),
                    ),
                    self.assertRaisesRegex(RuntimeError, "FTS indexing unavailable"),
                ):
                    catalog.import_corpus(corpus)
                stats = catalog.stats()
                runs = catalog.list_runs()
                errors = catalog.list_errors()

            self.assertEqual(stats.videos, 0)
            self.assertEqual(stats.sources, 0)
            self.assertEqual(stats.artifacts, 0)
            self.assertEqual(stats.runs, 1)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].status, "failed")
            self.assertEqual(runs[0].items_seen, 2)
            self.assertEqual(runs[0].items_written, 0)
            self.assertEqual(runs[0].error_count, 1)
            self.assertEqual(errors[0].stage, "corpus_import_run")
            self.assertIn("FTS indexing unavailable", errors[0].message)

    def test_database_error_is_fatal_instead_of_becoming_an_item_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="A valid artifact before a database failure.",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                with (
                    patch.object(
                        catalog,
                        "_upsert_video",
                        side_effect=sqlite3.OperationalError("database is full"),
                    ),
                    self.assertRaisesRegex(sqlite3.OperationalError, "database is full"),
                ):
                    catalog.import_corpus(corpus)
                runs = catalog.list_runs()
                errors = catalog.list_errors()

            self.assertEqual(runs[0].status, "failed")
            self.assertEqual(runs[0].error_count, 1)
            self.assertEqual(errors[0].stage, "corpus_import_run")
            self.assertIn("OperationalError: database is full", errors[0].message)

    def test_import_records_invalid_json_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="good-channel",
                language="en",
                transcript="A multilingual market signal hidden in the transcript.",
            )
            broken = corpus / "broken-channel" / "insights"
            broken.mkdir(parents=True)
            broken_path = broken / "20260819 - Broken metadata [bad123DEF45].en.json"
            broken_path.write_text("{not-json", encoding="utf-8")

            with Catalog(base / "catalog.sqlite3") as catalog:
                summary = catalog.import_corpus(corpus)
                stats = catalog.stats()
                errors = catalog.list_errors(run_id=summary.run_id)

            self.assertEqual(summary.status, "partial")
            self.assertEqual(summary.items_seen, 3)
            self.assertEqual(summary.items_written, 2)
            self.assertEqual(summary.error_count, 1)
            self.assertEqual(stats.videos, 1)
            self.assertEqual(stats.artifacts, 2)
            self.assertEqual(stats.errors, 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].stage, "corpus_import")
            self.assertEqual(
                errors[0].item_ref,
                "broken-channel/insights/20260819 - Broken metadata [bad123DEF45].en.json",
            )
            self.assertIn("JSON", errors[0].message)

    def test_import_keeps_type_invalid_insight_and_records_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            insights = corpus / "market-channel" / "insights"
            insights.mkdir(parents=True)
            path = insights / "20260818 - AI market update [type123ABCD].en.json"
            path.write_text(
                json.dumps(
                    {
                        "subject": {"name": "AI market"},
                        "key_points": ["Demand is shifting"],
                        "tools": [],
                        "advice": "Track primary evidence",
                        "quotes": [],
                    }
                ),
                encoding="utf-8",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                summary = catalog.import_corpus(corpus)
                stats = catalog.stats()
                errors = catalog.list_errors(run_id=summary.run_id)
                found = catalog.search("AI market")

            self.assertEqual(summary.status, "partial")
            self.assertEqual(summary.items_seen, 1)
            self.assertEqual(summary.items_written, 1)
            self.assertEqual(summary.error_count, 1)
            self.assertEqual(stats.videos, 1)
            self.assertEqual(stats.artifacts, 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0].stage, "corpus_validation")
            self.assertIn("subject must be a string", errors[0].message)
            self.assertIn("advice must be a list", errors[0].message)
            self.assertEqual([item.video_id for item in found], ["type123ABCD"])


class CatalogSearchTests(unittest.TestCase):
    def test_searches_title_insight_and_transcript_with_source_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="A multilingual market signal hidden in the transcript.",
            )

            with Catalog(base / "catalog.sqlite3") as catalog:
                catalog.import_corpus(corpus)
                by_title = catalog.search("agentic product")
                by_insight = catalog.search("evidence linked source")
                by_transcript = catalog.search("multilingual market signal")
                punctuation = catalog.search("AI (product) discovery?")
                included = catalog.search("product", source="product-channel")
                excluded = catalog.search("product", source="other-channel")

            for results in (by_title, by_insight, by_transcript, punctuation, included):
                self.assertEqual([item.video_id for item in results], [VIDEO_ID])
                self.assertEqual(results[0].sources, ("product-channel",))
                self.assertEqual(
                    results[0].watch_url,
                    f"https://www.youtube.com/watch?v={VIDEO_ID}",
                )
            self.assertEqual(excluded, [])

    def test_read_only_discovery_queries_are_bounded_stable_and_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="zeta-channel",
                language="en",
                transcript="A shared deterministic signal in a transcript.",
                video_id="zeta123ABCD",
            )
            _write_video_artifacts(
                corpus,
                channel="alpha-channel",
                language="en",
                transcript="A shared deterministic signal in a transcript.",
                video_id="alpha12ABCD",
            )
            database = base / "catalog.sqlite3"
            with Catalog(database) as catalog:
                catalog.import_corpus(corpus)
                catalog.checkpoint()

            before_database = database.read_bytes()
            before_names = sorted(path.name for path in base.iterdir())
            with Catalog.open_read_only(database) as reader:
                corpora = reader.list_corpora(limit=100)
                videos = reader.search_videos("shared deterministic", limit=20)

            self.assertEqual(
                [summary.source for summary in corpora],
                ["alpha-channel", "zeta-channel"],
            )
            self.assertEqual(
                [item.video_id for item in videos],
                sorted(item.video_id for item in videos),
            )
            self.assertTrue(all(item.highlight for item in videos))
            self.assertEqual(database.read_bytes(), before_database)
            self.assertEqual(sorted(path.name for path in base.iterdir()), before_names)

    def test_read_only_discovery_rejects_limits_outside_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "catalog.sqlite3"
            with Catalog(database) as catalog:
                catalog.checkpoint()

            with Catalog.open_read_only(database) as reader:
                with self.assertRaisesRegex(ValueError, "between 1 and 100"):
                    reader.list_corpora(limit=101)
                with self.assertRaisesRegex(ValueError, "between 1 and 100"):
                    reader.search_videos("query", limit=101)


class CatalogDiscoveryTests(unittest.TestCase):
    def test_unicode_handle_written_by_public_api_is_readable(self) -> None:
        source = "https://www.youtube.com/@日本語/videos"
        result = VideoListResult(
            videos=[
                VideoInfo(
                    video_id=VIDEO_ID,
                    title="Unicode source metadata",
                    upload_date="20260828",
                )
            ],
            errors=[],
            returncode=0,
        )

        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "catalog.sqlite3"
            with Catalog(database) as catalog:
                catalog.ingest_discovery(source, result)
                catalog.checkpoint()

            with Catalog.open_read_only(database) as reader:
                corpora = reader.list_corpora()
                videos = reader.search_videos("unicode source", source="日本語")

        self.assertEqual([corpus.source for corpus in corpora], ["日本語"])
        self.assertEqual([video.video_id for video in videos], [VIDEO_ID])
        self.assertEqual(videos[0].sources, ("日本語",))

    def test_public_writer_canonicalizes_hostile_source_slug_characters(self) -> None:
        cases = {
            "https://www.youtube.com/@safe\x00name/videos": "safe-name",
            "https://www.youtube.com/@safe\\name/videos": "safe-name",
            "https://www.youtube.com/@／etc／passwd/videos": "etc-passwd",
            r"C:\secret": "secret",
            "/absolute/path": "path",
        }

        for index, (source, expected_slug) in enumerate(cases.items()):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "catalog.sqlite3"
                video_id = f"safe{index:07d}"
                with Catalog(database) as catalog:
                    catalog.ingest_discovery(
                        source,
                        VideoListResult(
                            videos=[
                                VideoInfo(
                                    video_id=video_id,
                                    title="Safe canonical source",
                                    upload_date="20260828",
                                )
                            ],
                            errors=[],
                            returncode=0,
                        ),
                    )
                    catalog.checkpoint()

                with Catalog.open_read_only(database) as reader:
                    corpora = reader.list_corpora()
                    videos = reader.search_videos("safe canonical")

                self.assertEqual(
                    [corpus.source for corpus in corpora], [expected_slug]
                )
                self.assertEqual(videos[0].sources, (expected_slug,))
                self.assertNotIn("\x00", expected_slug)
                self.assertNotIn("/", expected_slug)
                self.assertNotIn("\\", expected_slug)

    def test_read_only_catalog_rejects_injected_hostile_source_slugs(self) -> None:
        hostile_slugs = (
            "safe/source",
            r"safe\source",
            "safe\x00source",
            "/absolute/source",
            r"C:\absolute\source",
        )

        for hostile_slug in hostile_slugs:
            with self.subTest(slug=hostile_slug), tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "catalog.sqlite3"
                with Catalog(database) as catalog:
                    catalog.ingest_discovery(
                        "https://www.youtube.com/@safe-source/videos",
                        VideoListResult(
                            videos=[
                                VideoInfo(
                                    video_id=VIDEO_ID,
                                    title="Safe metadata",
                                    upload_date="20260828",
                                )
                            ],
                            errors=[],
                            returncode=0,
                        ),
                    )
                    catalog.checkpoint()

                with sqlite3.connect(database) as connection:
                    connection.execute(
                        "UPDATE video_sources SET source_slug = ?",
                        (hostile_slug,),
                    )
                    connection.commit()
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

                with Catalog.open_read_only(database) as reader:
                    with self.assertRaisesRegex(CatalogError, "catalog row is invalid"):
                        reader.list_corpora()

    def test_discovery_upserts_videos_and_persists_partial_errors(self) -> None:
        result = VideoListResult(
            videos=[
                VideoInfo(
                    video_id="disc123ABCD",
                    title="Agent observability in production",
                    upload_date="20260824",
                ),
                VideoInfo(
                    video_id="disc567EFGH",
                    title="Product strategy without vanity metrics",
                    upload_date="20260823",
                ),
            ],
            errors=["ERROR: one private video was skipped"],
            returncode=0,
        )
        source = "https://www.youtube.com/@example/videos"

        with tempfile.TemporaryDirectory() as tmp:
            with Catalog(Path(tmp) / "catalog.sqlite3") as catalog:
                first = catalog.ingest_discovery(source, result)
                second = catalog.ingest_discovery(source, result)
                stats = catalog.stats()
                errors = catalog.list_errors()
                found = catalog.search("agent observability", source="example")

        self.assertEqual(first.status, "partial")
        self.assertEqual(first.items_seen, 2)
        self.assertEqual(first.items_written, 2)
        self.assertEqual(first.error_count, 1)
        self.assertEqual(second.status, "partial")
        self.assertEqual(second.items_seen, 2)
        self.assertEqual(second.items_written, 0)
        self.assertEqual(second.error_count, 1)
        self.assertEqual(stats.videos, 2)
        self.assertEqual(stats.sources, 2)
        self.assertEqual(stats.artifacts, 0)
        self.assertEqual(stats.runs, 2)
        self.assertEqual(stats.errors, 2)
        self.assertEqual([error.message for error in errors], result.errors * 2)
        self.assertEqual([item.video_id for item in found], ["disc123ABCD"])


if __name__ == "__main__":
    unittest.main()


def test_catalog_holds_cooperative_writer_lock_for_connection_lifetime(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    lock_path = tmp_path / ".catalog.sqlite3.lock"
    lock_path.touch()

    with Catalog(database):
        competing_fd = os.open(
            lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competing_fd)

    competing_fd = os.open(lock_path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(competing_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(competing_fd)


def test_catalog_constructor_rejects_database_symlink_before_sqlite_open(
    tmp_path: Path,
) -> None:
    database = tmp_path / "catalog.sqlite3"
    outside = tmp_path / "outside.sqlite3"
    database.symlink_to(outside)

    with pytest.raises(Exception) as raised:
        Catalog(database)

    assert raised.value.__class__.__name__ == "CatalogError"
    assert str(raised.value) == "catalog database path is unsafe"
    assert not outside.exists()


def test_read_only_catalog_uses_anchored_snapshot_during_parent_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.catalog as catalog_module

    live_parent = tmp_path / "live"
    replacement_parent = tmp_path / "replacement"
    moved_parent = tmp_path / "moved"
    live_database = live_parent / "catalog.sqlite3"
    replacement_database = replacement_parent / "catalog.sqlite3"
    with Catalog(live_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@original/videos",
            VideoListResult(
                videos=[VideoInfo(VIDEO_ID, "Original needle", "20260828")],
                errors=[],
                returncode=0,
            ),
        )
        catalog.checkpoint()
    with Catalog(replacement_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@replacement/videos",
            VideoListResult(
                videos=[VideoInfo(VIDEO_ID, "Replacement needle", "20260828")],
                errors=[],
                returncode=0,
            ),
        )
        catalog.checkpoint()

    original_connect = catalog_module.sqlite3.connect
    swapped = False

    def swapping_connect(database: object, *args: object, **kwargs: object):
        nonlocal swapped
        if not swapped and "immutable=1" in str(database):
            swapped = True
            live_parent.rename(moved_parent)
            replacement_parent.rename(live_parent)
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(catalog_module.sqlite3, "connect", swapping_connect)

    with Catalog.open_read_only(live_database) as reader:
        found = reader.search_videos("original needle")

    assert swapped is True
    assert [item.title for item in found] == ["Original needle"]


def test_imported_artifact_paths_survive_relocation_and_reject_escape_symlinks(
    tmp_path: Path,
) -> None:
    original_root = tmp_path / "original-corpus"
    relocated_root = tmp_path / "relocated-corpus"
    database = tmp_path / "catalog.sqlite3"
    _write_video_artifacts(
        original_root,
        channel="product-channel",
        language="en",
        transcript="Portable catalog artifact.",
    )

    with Catalog(database) as catalog:
        catalog.import_corpus(original_root)
        catalog.checkpoint()
    with sqlite3.connect(database) as connection:
        stored_paths = [
            row[0] for row in connection.execute("SELECT path FROM artifacts ORDER BY path")
        ]

    assert stored_paths == [
        f"product-channel/insights/20260820 - Agentic product discovery [{VIDEO_ID}].en.json",
        f"product-channel/transcripts/20260820 - Agentic product discovery [{VIDEO_ID}].en.vtt",
    ]
    original_root.rename(relocated_root)
    resolved = [Catalog.resolve_artifact_path(relocated_root, path) for path in stored_paths]
    assert all(path.is_file() for path in resolved)

    linked_path = relocated_root / "product-channel" / "linked-insight.json"
    linked_path.symlink_to(resolved[0])
    assert Catalog.resolve_artifact_path(
        relocated_root, "product-channel/linked-insight.json"
    ) == resolved[0].resolve(strict=True)

    outside = tmp_path / "outside.json"
    outside.write_text("outside", encoding="utf-8")
    resolved[0].unlink()
    resolved[0].symlink_to(outside)
    with pytest.raises(CatalogError):
        Catalog.resolve_artifact_path(relocated_root, stored_paths[0])

    root_link = tmp_path / "corpus-root-link"
    root_link.symlink_to(relocated_root, target_is_directory=True)
    with pytest.raises(CatalogError):
        Catalog.resolve_artifact_path(root_link, stored_paths[1])


@pytest.mark.parametrize("unsafe_path", ["../escape.vtt", "/absolute.vtt", r"C:\\escape.vtt"])
def test_read_only_catalog_rejects_unsafe_stored_artifact_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    corpus = tmp_path / "corpus"
    database = tmp_path / "catalog.sqlite3"
    _write_video_artifacts(
        corpus,
        channel="product-channel",
        language="en",
        transcript="Catalog path validation.",
    )
    with Catalog(database) as catalog:
        catalog.import_corpus(corpus)
        catalog.checkpoint()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE artifacts SET path = ?", (unsafe_path,))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    with pytest.raises(CatalogError):
        Catalog.open_read_only(database)


def test_read_only_catalog_rejects_pre_portability_schema(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    database = tmp_path / "catalog.sqlite3"
    _write_video_artifacts(
        corpus,
        channel="product-channel",
        language="en",
        transcript="Old schemas must fail closed.",
    )
    with Catalog(database) as catalog:
        catalog.import_corpus(corpus)
        catalog.checkpoint()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE schema_meta SET version = 1")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    with pytest.raises(CatalogError):
        Catalog.open_read_only(database)
