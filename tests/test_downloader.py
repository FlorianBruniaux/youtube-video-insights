from __future__ import annotations

import subprocess
from pathlib import Path

from yt_insights import downloader
from yt_insights.downloader import VideoInfo, download_subtitles, list_videos, vtt_to_video_info


def test_list_videos_parses_metadata_and_uses_argument_vector(monkeypatch) -> None:
    """Detects metadata loss or a shell-based yt-dlp invocation in video discovery."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="20260223|Build reliable agents|nfupYzLjFGc\ninvalid\n",
            stderr="",
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    videos = list_videos("https://www.youtube.com/@example", cookies_from_browser="firefox")

    assert videos == [
        VideoInfo(
            video_id="nfupYzLjFGc",
            title="Build reliable agents",
            upload_date="20260223",
        )
    ]
    assert calls == [
        (
            [
                "yt-dlp",
                "--flat-playlist",
                "--print",
                "%(upload_date)s|%(title)s|%(id)s",
                "--ignore-errors",
                "--cookies-from-browser",
                "firefox",
                "https://www.youtube.com/@example",
            ],
            {"capture_output": True, "text": True},
        )
    ]


def test_download_subtitles_returns_existing_vtt_and_preserves_argument_vector(
    tmp_path: Path, monkeypatch
) -> None:
    """Detects a regression that drops existing subtitle paths or invokes yt-dlp through a shell."""
    output_dir = tmp_path / "transcripts"
    output_dir.mkdir()
    vtt_path = output_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt"
    vtt_path.write_text("WEBVTT\n", encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"[info] Writing video subtitles to: {vtt_path}\n",
            stderr="",
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    result = download_subtitles(
        "https://www.youtube.com/watch?v=nfupYzLjFGc",
        output_dir,
        sleep_requests=2,
        cookies_from_browser="safari",
        sub_langs="fr,en",
    )

    assert result.vtt_files == [vtt_path]
    assert result.errors == []
    assert result.skipped_count == 0
    assert calls == [
        (
            [
                "yt-dlp",
                "--write-auto-subs",
                "--sub-langs",
                "fr,en",
                "--sub-format",
                "vtt",
                "--skip-download",
                "--ignore-errors",
                "--output",
                str(output_dir / "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
                "--extractor-retries",
                "5",
                "--retry-sleep",
                "extractor:exp=1:30",
                "--sleep-requests",
                "2",
                "--cookies-from-browser",
                "safari",
                "https://www.youtube.com/watch?v=nfupYzLjFGc",
            ],
            {"capture_output": True, "text": True},
        )
    ]


def test_vtt_to_video_info_parses_date_title_and_video_id() -> None:
    """Detects a filename parsing regression that breaks title or video-id selection."""
    result = vtt_to_video_info(
        Path("20260223 - Build reliable agents [nfupYzLjFGc].en.vtt")
    )

    assert result == VideoInfo(
        video_id="nfupYzLjFGc",
        title="Build reliable agents",
        upload_date="20260223",
    )
