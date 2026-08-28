from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yt_insights.cli_export import export_group


VIDEO_ID = "nfupYzLjFGc"


def _write_flat_corpus(root: Path, *languages: str) -> None:
    transcripts = root / "transcripts"
    transcripts.mkdir(parents=True)
    stem = f"Reliable agents [{VIDEO_ID}]"
    for language in languages:
        (transcripts / f"{stem}.{language}.vtt").write_text(
            "WEBVTT\n\n00:00:10.000 --> 00:00:12.000\nAgent source\n",
            encoding="utf-8",
        )
    (transcripts / f"{stem}.info.json").write_text(
        json.dumps(
            {
                "id": VIDEO_ID,
                "title": "Reliable agents",
                "channel": "Stable Channel",
                "channel_id": "UCStableChannel123",
            }
        ),
        encoding="utf-8",
    )


def test_export_video_json_uses_configured_data_root_from_unrelated_cwd(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write_flat_corpus(corpus, "fr")

    result = CliRunner().invoke(
        export_group,
        ["video", VIDEO_ID, "--format", "md", "--lang", "fr", "--json"],
        env={"YT_INSIGHTS_DATA_ROOT": str(corpus)},
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "format": "md",
        "language": "fr",
        "path": str((corpus / "exports" / f"{VIDEO_ID}.fr.md").resolve()),
        "source_sha256": payload["source_sha256"],
        "video_id": VIDEO_ID,
    }
    assert len(payload["source_sha256"]) == 64


def test_export_video_honors_external_configured_exports_directory(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    external_exports = tmp_path / "external-exports"
    _write_flat_corpus(corpus, "fr")

    result = CliRunner().invoke(
        export_group,
        ["video", VIDEO_ID, "--format", "md", "--lang", "fr", "--json"],
        env={
            "YT_INSIGHTS_DATA_ROOT": str(corpus),
            "YT_INSIGHTS_EXPORTS_DIR": str(external_exports),
        },
        catch_exceptions=False,
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["path"] == str(
        (external_exports / f"{VIDEO_ID}.fr.md").resolve()
    )
    assert Path(payload["path"]).is_file()


def test_export_video_requires_language_when_corpus_is_ambiguous(tmp_path: Path) -> None:
    _write_flat_corpus(tmp_path, "fr", "en")

    result = CliRunner().invoke(
        export_group,
        ["video", VIDEO_ID, "--data-root", str(tmp_path), "--format", "txt"],
    )

    assert result.exit_code == 1
    assert "Multiple transcript languages found" in result.output
    assert "--lang" in result.output


def test_export_video_supports_explicit_output_and_force(tmp_path: Path) -> None:
    _write_flat_corpus(tmp_path, "fr")
    target = tmp_path / "article-source.txt"
    target.write_text("existing", encoding="utf-8")
    runner = CliRunner()

    refused = runner.invoke(
        export_group,
        [
            "video",
            f"https://youtu.be/{VIDEO_ID}",
            "--data-root",
            str(tmp_path),
            "--format",
            "txt",
            "--output",
            str(target),
        ],
    )
    replaced = runner.invoke(
        export_group,
        [
            "video",
            VIDEO_ID,
            "--data-root",
            str(tmp_path),
            "--format",
            "txt",
            "--output",
            str(target),
            "--force",
        ],
    )

    assert refused.exit_code == 1
    assert "already exists" in refused.output
    assert replaced.exit_code == 0
    assert target.read_text(encoding="utf-8") == "Agent source\n"


def test_export_video_rejects_invalid_format_at_click_boundary(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        export_group,
        ["video", VIDEO_ID, "--data-root", str(tmp_path), "--format", "pdf"],
    )

    assert result.exit_code == 2
    assert "Invalid value for '--format'" in result.output
