from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from yt_insights import downloader
from yt_insights.downloader import (
    VideoInfo,
    download_subtitles,
    fetch_video_list,
    list_videos,
    vtt_to_video_info,
)


def test_list_videos_parses_metadata_and_uses_argument_vector(monkeypatch) -> None:
    """Detects metadata loss or a shell-based yt-dlp invocation in video discovery."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=json.dumps(
                {
                    "upload_date": "20260223",
                    "title": "Build | reliable agents",
                    "id": "nfupYzLjFGc",
                }
            ) + "\n",
            stderr="",
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    videos = list_videos("https://www.youtube.com/@example", cookies_from_browser="firefox")

    assert videos == [
        VideoInfo(
            video_id="nfupYzLjFGc",
            title="Build | reliable agents",
            upload_date="20260223",
        )
    ]
    assert calls == [
        (
            [
                "yt-dlp",
                "--dump-json",
                "--skip-download",
                "--no-flat-playlist",
                "--ignore-errors",
                "--cookies-from-browser",
                "firefox",
                "https://www.youtube.com/@example",
            ],
            {"capture_output": True, "text": True},
        )
    ]


@pytest.mark.parametrize(
    "log_template",
    [
        "[info] Writing video subtitles to: {path}",
        "[info] nfupYzLjFGc: Subtitle file already exists: {path}",
    ],
)
def test_download_subtitles_returns_logged_vtt_and_preserves_argument_vector(
    tmp_path: Path, monkeypatch, log_template: str
) -> None:
    """Detects a regression that drops written or already-existing subtitles."""
    output_dir = tmp_path / "transcripts"
    output_dir.mkdir()
    vtt_path = output_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt"
    cached = "already exists" in log_template
    if cached:
        vtt_path.write_text("WEBVTT\n", encoding="utf-8")
    calls: list[tuple[list[str], dict]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        staged_vtt = Path(args[args.index("--output") + 1]).parent / vtt_path.name
        if not cached:
            staged_vtt.write_text("WEBVTT\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=log_template.format(path=staged_vtt) + "\n",
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
    assert result.skipped_count == int(cached)
    assert result.returncode == 0
    assert len(calls) == 1
    actual_args, actual_kwargs = calls[0]
    staged_output = actual_args[actual_args.index("--output") + 1]
    assert Path(staged_output).parent != output_dir
    assert Path(staged_output).name == "%(upload_date)s - %(title)s [%(id)s].%(ext)s"
    assert actual_args == [
        "yt-dlp",
        "--write-auto-subs",
        "--sub-langs",
        "fr,en",
        "--sub-format",
        "vtt",
        "--skip-download",
        "--write-info-json",
        "--no-write-playlist-metafiles",
        "--ignore-errors",
        "--output",
        staged_output,
        "--extractor-retries",
        "5",
        "--retry-sleep",
        "extractor:exp=1:30",
        "--sleep-requests",
        "2",
        "--cookies-from-browser",
        "safari",
        "https://www.youtube.com/watch?v=nfupYzLjFGc",
    ]
    assert actual_kwargs == {"capture_output": True, "text": True}


def test_downloaded_flat_layout_indexes_channel_identity_from_info_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default output/transcripts layout must not become channel_id=output."""
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    output_dir = root / "transcripts"
    vtt_path = output_dir / "20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt"
    info_path = output_dir / "20260223 - Build reliable agents [nfupYzLjFGc].info.json"

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        assert "--write-info-json" in args
        assert "--no-write-playlist-metafiles" in args
        output_dir.mkdir(parents=True, exist_ok=True)
        vtt_path.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nlocal identity\n",
            encoding="utf-8",
        )
        info_path.write_text(
            json.dumps(
                {
                    "id": "nfupYzLjFGc",
                    "channel_id": "UCStableChannel1234567890",
                    "channel": "Stable Channel",
                }
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"[info] Writing video subtitles to: {vtt_path}\n",
            stderr="",
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    result = download_subtitles("https://youtu.be/nfupYzLjFGc", output_dir)
    manifest = scan_corpus(root)

    assert result.vtt_files == [vtt_path]
    assert len(manifest.documents) == 1
    assert manifest.documents[0].channel_id == "UCStableChannel1234567890"
    assert manifest.documents[0].channel_title == "Stable Channel"


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


def test_fetch_video_list_preserves_videos_and_external_errors() -> None:
    completed = SimpleNamespace(
        stdout=json.dumps(
            {
                "upload_date": "20260820",
                "title": "Agentic | Systems",
                "id": "abc123DEF45",
                "channel_id": "UCtopicDiscovery",
                "channel": "Topic Discovery",
                "description": "This must not enter the shared model.",
            }
        ) + "\n",
        stderr="WARNING: transient warning\nERROR: one item is unavailable\n",
        returncode=0,
    )

    with patch("yt_insights.downloader.subprocess.run", return_value=completed) as run:
        result = fetch_video_list("https://www.youtube.com/@example/videos")
        compatibility_videos = list_videos(
            "https://www.youtube.com/@example/videos"
        )

    assert run.call_count == 2
    assert result.returncode == 0
    assert result.errors == ["ERROR: one item is unavailable"]
    assert len(result.videos) == 1
    assert result.videos[0].video_id == "abc123DEF45"
    assert result.videos[0].title == "Agentic | Systems"
    assert result.videos[0].upload_date == "20260820"
    assert result.videos[0].channel_id == "UCtopicDiscovery"
    assert result.videos[0].channel_title == "Topic Discovery"
    assert compatibility_videos == result.videos


def test_fetch_video_list_discards_unbounded_or_nul_channel_metadata() -> None:
    completed = SimpleNamespace(
        stdout=json.dumps(
            {
                "upload_date": "20260820",
                "title": "Agentic | Systems",
                "id": "abc123DEF45",
                "channel_id": "x" * 301,
                "uploader": "unsafe\u0000channel",
            }
        )
        + "\n",
        stderr="",
        returncode=0,
    )

    with patch("yt_insights.downloader.subprocess.run", return_value=completed):
        result = fetch_video_list("ytsearch10:agentic systems")

    assert result.videos == [
        VideoInfo(
            video_id="abc123DEF45",
            title="Agentic | Systems",
            upload_date="20260820",
        )
    ]


def test_fetch_video_list_exposes_nonzero_exit_without_error_line() -> None:
    completed = SimpleNamespace(
        stdout="",
        stderr="connection refused\n",
        returncode=2,
    )

    with patch("yt_insights.downloader.subprocess.run", return_value=completed):
        result = fetch_video_list("https://www.youtube.com/@example/videos")

    assert result.videos == []
    assert result.returncode == 2
    assert result.errors == ["yt-dlp exited with status 2: connection refused"]


def test_download_subtitles_exposes_nonzero_exit_without_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        downloader.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=2, stdout="", stderr="connection refused"
        ),
    )

    result = download_subtitles("https://youtu.be/nfupYzLjFGc", tmp_path)

    assert result.returncode == 2
    assert result.errors == ["yt-dlp exited with status 2: connection refused"]


def test_download_subtitles_preseeds_cached_vtt_and_sidecar_without_counting_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "transcripts"
    output_dir.mkdir()
    stem = "20260820 - Cached [nfupYzLjFGc]"
    vtt = output_dir / f"{stem}.fr.vtt"
    info = output_dir / f"{stem}.info.json"
    vtt.write_text("WEBVTT\n", encoding="utf-8")
    info.write_text('{"id":"nfupYzLjFGc"}', encoding="utf-8")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        staging = Path(args[args.index("--output") + 1]).parent
        assert (staging / vtt.name).read_bytes() == b"WEBVTT\n"
        assert (staging / info.name).read_bytes() == b'{"id":"nfupYzLjFGc"}'
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    result = download_subtitles("https://youtu.be/nfupYzLjFGc", output_dir)

    assert result.vtt_files == [vtt]
    assert result.skipped_count == 1
    assert result.errors == []
    assert result.returncode == 0


def test_download_subtitles_returns_cached_vtt_with_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_dir = tmp_path / "transcripts"
    output_dir.mkdir()
    vtt = output_dir / "20260820 - Cached [nfupYzLjFGc].fr.vtt"
    vtt.write_text("WEBVTT\n", encoding="utf-8")

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        staging = Path(args[args.index("--output") + 1]).parent
        assert (staging / vtt.name).read_bytes() == b"WEBVTT\n"
        return subprocess.CompletedProcess(
            args=args, returncode=2, stdout="", stderr="connection refused"
        )

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)

    result = download_subtitles("https://youtu.be/nfupYzLjFGc", output_dir)

    assert result.vtt_files == [vtt]
    assert result.skipped_count == 1
    assert result.errors == ["yt-dlp exited with status 2: connection refused"]
    assert result.returncode == 2


def test_download_subtitles_rejects_symlink_output_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "transcripts"
    link.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        downloader.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run"),
    )

    with pytest.raises(ValueError, match="symlink"):
        download_subtitles("https://youtu.be/nfupYzLjFGc", link)


def test_download_promotes_through_held_dirfd_when_path_is_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "corpus"
    destination = root / "transcripts"
    destination.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    original = root / "original-transcripts"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        template = Path(args[args.index("--output") + 1])
        staged = template.parent / "20260820 - Stable [nfupYzLjFGc].fr.vtt"
        staged.write_text("WEBVTT\n", encoding="utf-8")
        destination.rename(original)
        destination.symlink_to(outside, target_is_directory=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(downloader.subprocess, "run", fake_run)
    result = download_subtitles(
        "https://youtu.be/nfupYzLjFGc", destination, data_root=root
    )

    assert result.errors == []
    assert not any(outside.iterdir())
    assert (original / "20260820 - Stable [nfupYzLjFGc].fr.vtt").is_file()
