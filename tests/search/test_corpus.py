from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest


def _write_vtt(path: Path, *cues: tuple[int, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["WEBVTT", "Kind: captions", "Language: fr", ""]
    for start, text in cues:
        lines.extend(
            [
                f"00:00:{start:02d}.000 --> 00:00:{start + 5:02d}.000",
                text,
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def test_scan_corpus_orders_sources_and_builds_stable_manifest(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    _write_vtt(
        root / "zeta" / "transcripts" / "20260827 - Zed [ZedVideo123].FR.vtt",
        (0, "zeta transcript"),
    )
    _write_vtt(
        root / "alpha" / "transcripts" / "Alpha [AlphaVid_12].fr-CA.vtt",
        (0, "alpha transcript"),
    )

    first = scan_corpus(root)
    second = scan_corpus(root)

    assert first == second
    assert first.sources_discovered == first.sources_selected == 2
    assert first.sources_invalid == 0
    assert [document.source_relpath for document in first.documents] == [
        "alpha/transcripts/Alpha [AlphaVid_12].fr-CA.vtt",
        "zeta/transcripts/20260827 - Zed [ZedVideo123].FR.vtt",
    ]
    assert [(document.channel_id, document.channel_title) for document in first.documents] == [
        ("alpha", "alpha"),
        ("zeta", "zeta"),
    ]
    assert [(document.video_title, document.language) for document in first.documents] == [
        ("Alpha", "fr-ca"),
        ("Zed", "fr"),
    ]
    assert [passage.ordinal for passage in first.passages] == [0, 0]


def test_scan_corpus_applies_limit_before_classifying_invalid_sources(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    _write_vtt(root / "alpha" / "transcripts" / "bad-name.vtt", (0, "ignored"))
    _write_vtt(
        root / "zeta" / "transcripts" / "Good [GoodVideo12].fr.vtt", (0, "kept")
    )

    manifest = scan_corpus(root, limit=1)

    assert (manifest.sources_discovered, manifest.sources_selected, manifest.sources_invalid) == (2, 1, 1)
    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("alpha/transcripts/bad-name.vtt", "unsupported_filename")
    ]


def test_scan_corpus_defaults_to_the_first_fifty_sorted_sources(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    for index in range(51):
        _write_vtt(
            root / "alpha" / "transcripts" / f"Video {index:02d} [Video{index:06d}].fr.vtt",
            (0, "transcript"),
        )

    manifest = scan_corpus(root)

    assert (manifest.sources_discovered, manifest.sources_selected) == (51, 50)
    assert manifest.documents[-1].source_relpath.endswith("Video 49 [Video000049].fr.vtt")


def test_scan_corpus_classifies_a_blank_filename_title(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    _write_vtt(root / "alpha" / "transcripts" / "   [BlankVid123].fr.vtt", (0, "text"))

    manifest = scan_corpus(root)

    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("alpha/transcripts/   [BlankVid123].fr.vtt", "unsupported_filename")
    ]


def test_scan_corpus_classifies_empty_and_non_utf8_files(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    empty = root / "alpha" / "transcripts" / "Empty [EmptyVid123].fr.vtt"
    _write_vtt(empty)
    binary = root / "beta" / "transcripts" / "Bytes [BytesVid123].fr.vtt"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\xff\xfe")

    manifest = scan_corpus(root)

    assert manifest.sources_invalid == 2
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("alpha/transcripts/Empty [EmptyVid123].fr.vtt", "empty_segments"),
        ("beta/transcripts/Bytes [BytesVid123].fr.vtt", "invalid_utf8"),
    ]


def test_scan_corpus_hashes_the_exact_crlf_source_bytes(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    source = tmp_path / "output" / "alpha" / "transcripts" / "CRLF [CrlfVideo12].fr.vtt"
    source.parent.mkdir(parents=True)
    source.write_bytes(
        b"WEBVTT\r\nKind: captions\r\nLanguage: fr\r\n\r\n"
        b"00:00:00.000 --> 00:00:05.000\r\nCRLF transcript\r\n"
    )

    manifest = scan_corpus(tmp_path / "output")

    assert manifest.documents[0].source_sha256 == sha256(source.read_bytes()).hexdigest()


def test_scan_corpus_rejects_a_source_changed_during_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import corpus

    source = tmp_path / "output" / "alpha" / "transcripts" / "Race [RaceVideo12].fr.vtt"
    _write_vtt(source, (0, "original transcript"))
    original_parse = corpus.parse_vtt_timestamped

    def parse_then_change(path: Path) -> list[dict]:
        segments = original_parse(path)
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return segments

    monkeypatch.setattr(corpus, "parse_vtt_timestamped", parse_then_change)

    manifest = corpus.scan_corpus(tmp_path / "output")

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("alpha/transcripts/Race [RaceVideo12].fr.vtt", "source_changed_during_parse")
    ]


def test_scan_corpus_rejects_symlink_escaping_root(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    escaped = tmp_path / "outside.vtt"
    _write_vtt(escaped, (0, "external transcript"))
    transcript_dir = root / "alpha" / "transcripts"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "Link [LinkVideo12].fr.vtt").symlink_to(escaped)

    manifest = scan_corpus(root)

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("alpha/transcripts/Link [LinkVideo12].fr.vtt", "outside_corpus_root")
    ]


@pytest.mark.parametrize("limit", [0, 51, -1])
def test_scan_corpus_rejects_out_of_range_limits(tmp_path: Path, limit: int) -> None:
    from yt_insights.search.corpus import scan_corpus

    with pytest.raises(ValueError, match="limit"):
        scan_corpus(tmp_path, limit=limit)


def test_scan_corpus_rejects_a_missing_or_non_directory_root(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    with pytest.raises(ValueError, match="corpus_root"):
        scan_corpus(tmp_path / "missing")
    file_root = tmp_path / "file"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="corpus_root"):
        scan_corpus(file_root)
