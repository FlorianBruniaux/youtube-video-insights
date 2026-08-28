from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yt_insights.cli import cli
from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import BuildReport
from yt_insights.search.preflight import IndexSpacePreflightReport, InsufficientIndexSpace


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
    monkeypatch.chdir(tmp_path)
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


def test_index_defaults_to_ordered_phase_slice_of_50(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    calls: list[tuple[int | None, str]] = []

    def fake_scan(root: Path, *, limit: int | None, selection: str) -> CorpusManifest:
        calls.append((limit, selection))
        return CorpusManifest((), (), (), 81, 0, 0)

    monkeypatch.setattr(cli_search, "scan_corpus", fake_scan)

    result = CliRunner().invoke(
        cli,
        ["index", "--corpus-root", str(tmp_path), "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(50, "ordered")]


def test_index_representative_passes_the_exact_requested_slice(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    calls: list[tuple[int | None, str]] = []

    def fake_scan(root: Path, *, limit: int | None, selection: str) -> CorpusManifest:
        calls.append((limit, selection))
        return CorpusManifest((), (), (), 81, 17, 0)

    monkeypatch.setattr(cli_search, "scan_corpus", fake_scan)

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(tmp_path),
            "--limit",
            "17",
            "--selection",
            "representative",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(17, "representative")]


def test_index_all_dry_run_scans_every_source_without_preflight(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    calls: list[tuple[str, object]] = []

    def fail_preflight(*args, **kwargs) -> None:
        raise AssertionError("dry-run must not run the capacity preflight")

    def fake_scan(root: Path, *, limit: int | None, selection: str) -> CorpusManifest:
        calls.append(("scan", (limit, selection)))
        return CorpusManifest((), (), (), 3270, 3270, 0)

    monkeypatch.setattr(cli_search, "preflight_index_space", fail_preflight)
    monkeypatch.setattr(cli_search, "scan_corpus", fake_scan)

    result = CliRunner().invoke(
        cli,
        ["index", "--corpus-root", str(tmp_path), "--all", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [("scan", (None, "ordered"))]
    assert "Sources discovered: 3270" in result.output
    assert "Sources selected: 3270" in result.output


def test_index_all_runs_preflight_before_scan_and_reports_capacity(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    database = tmp_path / "search" / "search-v1.sqlite3"
    calls: list[str] = []

    def fake_preflight(root: Path, database_path: Path) -> IndexSpacePreflightReport:
        calls.append("preflight")
        assert root == tmp_path
        assert database_path == database
        return IndexSpacePreflightReport(
            corpus_root=root,
            database_path=database_path,
            disk_usage_path=tmp_path,
            sources_discovered=3270,
            source_files=3268,
            sources_excluded=2,
            source_bytes=1_073_741_824,
            required_bytes=2_147_483_648,
            available_bytes=10_737_418_240,
        )

    def fake_scan(root: Path, *, limit: int | None, selection: str) -> CorpusManifest:
        calls.append("scan")
        assert (limit, selection) == (None, "ordered")
        return CorpusManifest((), (), (), 3270, 3270, 0)

    def fake_rebuild(self, manifest: CorpusManifest) -> BuildReport:
        calls.append("rebuild")
        return BuildReport(3270, 3270, 0, 3270, 100_000)

    monkeypatch.setattr(cli_search, "preflight_index_space", fake_preflight)
    monkeypatch.setattr(cli_search, "scan_corpus", fake_scan)
    monkeypatch.setattr(cli_search.SQLiteFtsIndex, "rebuild", fake_rebuild)

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(tmp_path),
            "--database",
            str(database),
            "--all",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == ["preflight", "scan", "rebuild"]
    assert "Preflight candidates discovered: 3270" in result.output
    assert "Preflight regular files sized: 3268" in result.output
    assert "Preflight candidates excluded: 2" in result.output
    assert "Preflight source bytes: 1073741824" in result.output
    assert "Preflight required bytes: 2147483648" in result.output
    assert "Preflight available bytes: 10737418240" in result.output
    assert "Sources discovered: 3270" in result.output
    assert "Passages: 100000" in result.output


def test_index_limited_build_does_not_inventory_full_corpus(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    root = tmp_path / "output"
    database = root / ".search" / "search-v1.sqlite3"
    _write_vtt(
        _source_path(root, "alpha", "Reliable search", "VideoId_123", "en"),
        (0, "safe deterministic search results"),
    )

    def fail_preflight(*args, **kwargs) -> None:
        raise AssertionError("a limited build must not inventory the full corpus")

    monkeypatch.setattr(cli_search, "preflight_index_space", fail_preflight)

    result = CliRunner().invoke(
        cli,
        ["index", "--corpus-root", str(root), "--database", str(database)],
    )

    assert result.exit_code == 0, result.output
    assert database.is_file()


def test_index_preflight_failure_preserves_existing_database_without_scanning(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import cli_search

    database = tmp_path / "search-v1.sqlite3"
    database.write_bytes(b"existing index")

    def fail_preflight(root: Path, database_path: Path) -> None:
        raise InsufficientIndexSpace(
            "insufficient free disk space for search-index rebuild: "
            "available 128 bytes, required 256 bytes"
        )

    def fail_scan(*args, **kwargs) -> None:
        raise AssertionError("a failed preflight must stop before corpus parsing")

    def fail_rebuild(*args, **kwargs) -> None:
        raise AssertionError("a failed preflight must stop before rebuilding")

    monkeypatch.setattr(cli_search, "preflight_index_space", fail_preflight)
    monkeypatch.setattr(cli_search, "scan_corpus", fail_scan)
    monkeypatch.setattr(cli_search.SQLiteFtsIndex, "rebuild", fail_rebuild)

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(tmp_path),
            "--database",
            str(database),
            "--all",
        ],
    )

    assert result.exit_code != 0
    assert "insufficient free disk space" in result.output.lower()
    assert "free space" in result.output.lower()
    assert database.read_bytes() == b"existing index"


def test_index_system_preflight_failure_is_actionable_and_preserves_database(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import cli_search

    database = tmp_path / "search-v1.sqlite3"
    database.write_bytes(b"existing index")
    sensitive_detail = "/private/secret-corpus: permission denied"

    def fail_preflight(*args, **kwargs) -> None:
        raise OSError(sensitive_detail)

    def fail_scan(*args, **kwargs) -> None:
        raise AssertionError("a failed preflight must stop before corpus parsing")

    def fail_rebuild(*args, **kwargs) -> None:
        raise AssertionError("a failed preflight must stop before rebuilding")

    monkeypatch.setattr(cli_search, "preflight_index_space", fail_preflight)
    monkeypatch.setattr(cli_search, "scan_corpus", fail_scan)
    monkeypatch.setattr(cli_search.SQLiteFtsIndex, "rebuild", fail_rebuild)

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(tmp_path),
            "--database",
            str(database),
            "--all",
        ],
    )

    assert result.exit_code != 0
    assert "Cannot verify index capacity" in result.output
    assert sensitive_detail not in result.output
    assert not isinstance(result.exception, OSError)
    assert database.read_bytes() == b"existing index"


def test_index_rejects_ambiguous_all_and_status_combinations(tmp_path: Path) -> None:
    runner = CliRunner()
    base = ["index", "--corpus-root", str(tmp_path)]

    results = [
        runner.invoke(cli, [*base, "--all", "--limit", "10"]),
        runner.invoke(cli, [*base, "--all", "--selection", "representative"]),
        runner.invoke(cli, [*base, "--all", "--selection", "ordered"]),
        runner.invoke(cli, [*base, "--status", "--all"]),
        runner.invoke(cli, [*base, "--status", "--limit", "10"]),
        runner.invoke(cli, [*base, "--status", "--selection", "representative"]),
    ]

    assert all(result.exit_code != 0 for result in results)
    assert "--all cannot be used with an explicit --limit" in results[0].output
    assert "--all cannot be used with an explicit --selection" in results[1].output
    assert "--all cannot be used with an explicit --selection" in results[2].output
    assert all("--status" in result.output for result in results[3:])


def test_index_all_ignores_non_cli_default_map_slice_options(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    calls: list[tuple[int | None, str]] = []

    def fake_scan(root: Path, *, limit: int | None, selection: str) -> CorpusManifest:
        calls.append((limit, selection))
        return CorpusManifest((), (), (), 81, 81, 0)

    monkeypatch.setattr(cli_search, "scan_corpus", fake_scan)

    result = CliRunner().invoke(
        cli,
        ["index", "--corpus-root", str(tmp_path), "--all", "--dry-run"],
        default_map={"index": {"limit": 7, "selection": "representative"}},
    )

    assert result.exit_code == 0, result.output
    assert calls == [(None, "ordered")]


def test_index_status_ignores_non_cli_default_map_build_options(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import cli_search

    def fake_status(self) -> BuildReport:
        return BuildReport(50, 50, 0, 50, 100)

    monkeypatch.setattr(cli_search.SQLiteFtsIndex, "status", fake_status)

    result = CliRunner().invoke(
        cli,
        ["index", "--database", str(tmp_path / "search.sqlite3"), "--status"],
        default_map={"index": {"limit": 7, "selection": "representative"}},
    )

    assert result.exit_code == 0, result.output
    assert "Sources selected: 50" in result.output


def test_index_all_reports_preflight_exclusions_and_scan_invalid_sources(tmp_path: Path) -> None:
    root = tmp_path / "output"
    database = root / ".search" / "search-v1.sqlite3"
    regular = _source_path(root, "alpha", "Reliable search", "VideoId_123", "en")
    _write_vtt(
        regular,
        (0, "safe deterministic search results"),
    )
    symlink = _source_path(root, "alpha", "Linked search", "LinkVideo12", "en")
    symlink.symlink_to(regular)

    result = CliRunner().invoke(
        cli,
        [
            "index",
            "--corpus-root",
            str(root),
            "--database",
            str(database),
            "--all",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Preflight candidates discovered: 2\n" in result.output
    assert "Preflight regular files sized: 1\n" in result.output
    assert "Preflight candidates excluded: 1\n" in result.output
    assert "Sources discovered: 2\n" in result.output
    assert "Sources invalid: 1\n" in result.output


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


def test_search_distinguishes_a_corrupt_index_and_recommends_rebuild(tmp_path: Path) -> None:
    database = tmp_path / "corrupt.sqlite3"
    database.write_bytes(b"not a sqlite database")

    result = CliRunner().invoke(cli, ["search", "safe", "--database", str(database)])

    assert result.exit_code != 0
    assert "invalid" in result.output.lower()
    assert "rebuild" in result.output.lower()
    assert "Search request is invalid" not in result.output
