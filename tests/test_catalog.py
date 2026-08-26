from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yt_insights.catalog import Catalog
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
            self.assertEqual(errors[0].item_ref, str(broken_path.resolve()))
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


class CatalogDiscoveryTests(unittest.TestCase):
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
