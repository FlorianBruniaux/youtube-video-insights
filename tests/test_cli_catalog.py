from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from tests.test_catalog import VIDEO_ID, _write_video_artifacts
from yt_insights.cli import cli
from yt_insights.downloader import VideoInfo, VideoListResult


class CatalogCliTests(unittest.TestCase):
    def test_import_search_stats_and_errors_commands(self) -> None:
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            db = base / "catalog.sqlite3"
            corpus = base / "corpus"
            _write_video_artifacts(
                corpus,
                channel="product-channel",
                language="en",
                transcript="A multilingual market signal for newsletter research.",
            )
            broken_dir = corpus / "broken-channel" / "insights"
            broken_dir.mkdir(parents=True)
            broken = broken_dir / "20260819 - Broken metadata [bad123DEF45].en.json"
            broken.write_text("{broken", encoding="utf-8")

            first = runner.invoke(
                cli,
                ["catalog", "import-corpus", str(corpus), "--db", str(db)],
            )
            second = runner.invoke(
                cli,
                ["catalog", "import-corpus", str(corpus), "--db", str(db)],
            )
            search = runner.invoke(
                cli,
                [
                    "catalog",
                    "search",
                    "market signal",
                    "--source",
                    "product-channel",
                    "--db",
                    str(db),
                ],
            )
            stats = runner.invoke(cli, ["catalog", "stats", "--db", str(db)])
            errors = runner.invoke(cli, ["catalog", "errors", "--db", str(db)])
            empty = runner.invoke(
                cli,
                ["catalog", "search", "", "--db", str(db)],
            )

        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("run=1 status=partial seen=3 written=2 errors=1", first.output)
        self.assertEqual(second.exit_code, 0, second.output)
        self.assertIn("run=2 status=partial seen=3 written=0 errors=1", second.output)

        self.assertEqual(search.exit_code, 0, search.output)
        self.assertIn(VIDEO_ID, search.output)
        self.assertIn("Agentic product discovery", search.output)
        self.assertIn("product-channel", search.output)

        self.assertEqual(stats.exit_code, 0, stats.output)
        self.assertIn("videos=1", stats.output)
        self.assertIn("sources=1", stats.output)
        self.assertIn("artifacts=2", stats.output)
        self.assertIn("runs=2", stats.output)
        self.assertIn("errors=2", stats.output)

        self.assertEqual(errors.exit_code, 0, errors.output)
        self.assertIn("JSONDecodeError", errors.output)
        self.assertIn(broken.name, errors.output)

        self.assertEqual(empty.exit_code, 2, empty.output)
        self.assertIn("Search query cannot be empty", empty.output)

    def test_discover_command_persists_structured_result(self) -> None:
        runner = CliRunner()
        discovered = VideoListResult(
            videos=[
                VideoInfo(
                    video_id="disc123ABCD",
                    title="Agent observability in production",
                    upload_date="20260824",
                )
            ],
            errors=[],
            returncode=0,
        )
        source = "https://www.youtube.com/@example/videos"

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite3"
            with patch(
                "yt_insights.downloader.fetch_video_list",
                return_value=discovered,
            ):
                result = runner.invoke(
                    cli,
                    ["catalog", "discover", source, "--db", str(db)],
                )
            search = runner.invoke(
                cli,
                [
                    "catalog",
                    "search",
                    "agent observability",
                    "--db",
                    str(db),
                ],
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("status=completed", result.output)
        self.assertIn("seen=1 written=1 errors=0", result.output)
        self.assertEqual(search.exit_code, 0, search.output)
        self.assertIn("disc123ABCD", search.output)


if __name__ == "__main__":
    unittest.main()
