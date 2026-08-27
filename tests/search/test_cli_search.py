from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yt_insights.cli import cli


def _write_vtt(path: Path, *segments: tuple[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cues = "\n".join(
        f"00:00:{start:02d}.000 --> 00:00:{start + 4:02d}.000\n{text}"
        for start, text in segments
    )
    path.write_text(f"WEBVTT\n\n{cues}\n", encoding="utf-8")


def _source_path(root: Path, channel: str, title: str, video_id: str, language: str) -> Path:
    return root / channel / "transcripts" / f"{title} [{video_id}].{language}.vtt"


def _build_index(runner: CliRunner, root: Path, database: Path) -> None:
    result = runner.invoke(
        cli,
        ["index", "--corpus-root", str(root), "--database", str(database)],
    )
    assert result.exit_code == 0, result.output


def test_index_dry_run_reports_counts_without_creating_search_directory(tmp_path: Path) -> None:
    root = tmp_path / "output"
    database = root / ".search" / "search-v1.sqlite3"
    _write_vtt(
        _source_path(root, "alpha", "Reliable search", "VideoId_123", "en"),
        (0, "safe deterministic search results"),
    )

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(root),
            "--database",
            str(database),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.output == (
        "Sources discovered: 1\n"
        "Sources selected: 1\n"
        "Sources invalid: 0\n"
        "Documents: 1\n"
        "Passages: 1\n"
    )
    assert not database.exists()
    assert not database.parent.exists()


def test_index_builds_default_database_and_status_does_not_scan(tmp_path: Path, monkeypatch) -> None:
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        root = Path("output")
        _write_vtt(
            _source_path(root, "alpha", "Reliable search", "VideoId_123", "en"),
            (12, "safe deterministic search results"),
        )

        build = runner.invoke(cli, ["index"])

        assert build.exit_code == 0, build.output
        assert Path("output/.search/search-v1.sqlite3").is_file()

        from yt_insights import cli_search

        def fail_scan(*args, **kwargs) -> None:
            raise AssertionError("--status must not scan the corpus")

        monkeypatch.setattr(cli_search, "scan_corpus", fail_scan)
        status = runner.invoke(cli, ["index", "--status"])

    assert status.exit_code == 0, status.output
    assert status.output == (
        "Sources discovered: 1\n"
        "Sources selected: 1\n"
        "Sources invalid: 0\n"
        "Documents: 1\n"
        "Passages: 1\n"
    )


def test_index_rejects_invalid_slice_options_before_writing(tmp_path: Path) -> None:
    database = tmp_path / "output" / ".search" / "search-v1.sqlite3"
    runner = CliRunner()

    too_many = runner.invoke(
        cli,
        ["index", "--corpus-root", str(tmp_path), "--database", str(database), "--limit", "51"],
    )
    non_positive = runner.invoke(
        cli,
        ["index", "--corpus-root", str(tmp_path), "--database", str(database), "--limit", "0"],
    )
    incompatible = runner.invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(tmp_path),
            "--database",
            str(database),
            "--dry-run",
            "--status",
        ],
    )

    assert too_many.exit_code != 0
    assert "1" in too_many.output and "50" in too_many.output
    assert non_positive.exit_code != 0
    assert incompatible.exit_code != 0
    assert "cannot be used together" in incompatible.output
    assert not database.exists()
    assert not database.parent.exists()


def test_search_renders_filtered_text_and_deterministic_json(tmp_path: Path) -> None:
    root = tmp_path / "output"
    database = root / ".search" / "search-v1.sqlite3"
    _write_vtt(
        _source_path(root, "alpha", "Alpha result", "AlphaVid123", "en"),
        (12, "safe retrieval for alpha users"),
    )
    _write_vtt(
        _source_path(root, "beta", "Beta result", "BetaVid_123", "fr"),
        (30, "safe retrieval for beta users"),
    )
    runner = CliRunner()
    _build_index(runner, root, database)

    text_result = runner.invoke(
        cli,
        ["search", "safe retrieval", "--database", str(database), "--channel", "beta", "--lang", "fr"],
    )
    json_result = runner.invoke(
        cli,
        [
            "search",
            "safe retrieval",
            "--database",
            str(database),
            "--channel",
            "beta",
            "--lang",
            "fr",
            "--json",
        ],
    )

    assert text_result.exit_code == 0, text_result.output
    assert "Channel: beta" in text_result.output
    assert "Title: Beta result" in text_result.output
    assert "Language: fr" in text_result.output
    assert "Excerpt: safe retrieval for beta users" in text_result.output
    assert "Timestamp: 00:00:30" in text_result.output
    assert "URL: https://youtube.com/watch?v=BetaVid_123&t=30s" in text_result.output
    assert "Source: beta/transcripts/Beta result [BetaVid_123].fr.vtt" in text_result.output
    assert "alpha" not in text_result.output
    assert json_result.exit_code == 0, json_result.output
    assert json_result.output == json.dumps(
        {
            "hits": [
                {
                    "rank": 1,
                    "channel": "beta",
                    "title": "Beta result",
                    "language": "fr",
                    "excerpt": "safe retrieval for beta users",
                    "timestamp": "00:00:30",
                    "url": "https://youtube.com/watch?v=BetaVid_123&t=30s",
                    "source": "beta/transcripts/Beta result [BetaVid_123].fr.vtt",
                }
            ]
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def test_search_reports_missing_index_and_hides_hostile_query(tmp_path: Path) -> None:
    runner = CliRunner()
    missing = runner.invoke(cli, ["search", "safe", "--database", str(tmp_path / "missing.sqlite3")])

    root = tmp_path / "output"
    database = root / ".search" / "search-v1.sqlite3"
    _write_vtt(
        _source_path(root, "alpha", "Reliable search", "VideoId_123", "en"),
        (0, "safe deterministic search results"),
    )
    _build_index(runner, root, database)
    hostile_query = '" - : *'
    hostile = runner.invoke(cli, ["search", hostile_query, "--database", str(database)])

    assert missing.exit_code != 0
    assert "does not exist" in missing.output
    assert hostile.exit_code != 0
    assert "Search request is invalid" in hostile.output
    assert hostile_query not in hostile.output
