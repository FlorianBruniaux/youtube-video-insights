from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from yt_insights.downloader import fetch_video_list, list_videos


class VideoDiscoveryTests(unittest.TestCase):
    def test_fetch_video_list_preserves_videos_and_external_errors(self) -> None:
        completed = SimpleNamespace(
            stdout="20260820|Agentic Systems|abc123DEF45\nmalformed\n",
            stderr="WARNING: transient warning\nERROR: one item is unavailable\n",
            returncode=0,
        )

        with patch("yt_insights.downloader.subprocess.run", return_value=completed) as run:
            result = fetch_video_list("https://www.youtube.com/@example/videos")
            compatibility_videos = list_videos(
                "https://www.youtube.com/@example/videos"
            )

        self.assertEqual(run.call_count, 2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.errors, ["ERROR: one item is unavailable"])
        self.assertEqual(len(result.videos), 1)
        self.assertEqual(result.videos[0].video_id, "abc123DEF45")
        self.assertEqual(result.videos[0].title, "Agentic Systems")
        self.assertEqual(result.videos[0].upload_date, "20260820")
        self.assertEqual(compatibility_videos, result.videos)

    def test_fetch_video_list_exposes_nonzero_exit_without_error_line(self) -> None:
        completed = SimpleNamespace(
            stdout="",
            stderr="connection refused\n",
            returncode=2,
        )

        with patch("yt_insights.downloader.subprocess.run", return_value=completed):
            result = fetch_video_list("https://www.youtube.com/@example/videos")

        self.assertEqual(result.videos, [])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.errors,
            ["yt-dlp exited with status 2: connection refused"],
        )


if __name__ == "__main__":
    unittest.main()
