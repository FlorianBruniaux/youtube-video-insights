from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import socket
import tempfile

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


def _write_info(
    vtt_path: Path,
    *,
    channel_id: str = "UCStableChannel1234567890",
    channel: str = "Stable Channel",
    video_id: str | None = None,
) -> Path:
    language = vtt_path.name.rsplit(".", 2)[1]
    info_path = vtt_path.with_name(
        vtt_path.name.removesuffix(f".{language}.vtt") + ".info.json"
    )
    info_path.write_text(
        json.dumps(
            {
                "id": video_id or vtt_path.name.rsplit("[", 1)[1].split("]", 1)[0],
                "channel_id": channel_id,
                "channel": channel,
            }
        ),
        encoding="utf-8",
    )
    return info_path


def test_scan_corpus_uses_yt_dlp_metadata_for_flat_default_layout(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    french = root / "transcripts" / "Flat [FlatVideo12].fr.vtt"
    english = root / "transcripts" / "Flat [FlatVideo12].en.vtt"
    _write_vtt(french, (0, "français"))
    _write_vtt(english, (0, "english"))
    info_path = _write_info(french)

    manifest = scan_corpus(root)

    assert manifest.sources_invalid == 0
    assert info_path.is_file()
    assert [(item.channel_id, item.channel_title, item.language) for item in manifest.documents] == [
        ("UCStableChannel1234567890", "Stable Channel", "en"),
        ("UCStableChannel1234567890", "Stable Channel", "fr"),
    ]
    assert all(document.channel_id != "output" for document in manifest.documents)


def test_scan_corpus_rejects_flat_layout_without_channel_metadata(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    source = root / "transcripts" / "Missing [MissingVid1].fr.vtt"
    _write_vtt(source, (0, "missing metadata"))

    manifest = scan_corpus(root)

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("transcripts/Missing [MissingVid1].fr.vtt", "missing_channel_metadata")
    ]


@pytest.mark.parametrize(
    ("payload", "reason"),
    (
        ("not-json", "invalid_channel_metadata"),
        ('{"id":"WrongVideo1","channel_id":"UCgood","channel":"Good"}', "invalid_channel_metadata"),
        ('{"id":"HostileVid1","channel_id":"","channel":"Good"}', "invalid_channel_metadata"),
        ('{"id":"HostileVid1","channel_id":"UCgood","channel":"Bad\\u0000Title"}', "invalid_channel_metadata"),
    ),
)
def test_scan_corpus_rejects_invalid_flat_channel_metadata(
    tmp_path: Path, payload: str, reason: str
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    source = root / "transcripts" / "Hostile [HostileVid1].fr.vtt"
    _write_vtt(source, (0, "hostile metadata"))
    language = "fr"
    info_path = source.with_name(
        source.name.removesuffix(f".{language}.vtt") + ".info.json"
    )
    info_path.write_text(payload, encoding="utf-8")

    manifest = scan_corpus(root)

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("transcripts/Hostile [HostileVid1].fr.vtt", reason)
    ]


def test_scan_corpus_rejects_symlinked_flat_channel_metadata(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    source = root / "transcripts" / "Linked [LinkedVid12].fr.vtt"
    _write_vtt(source, (0, "linked metadata"))
    external = tmp_path / "external.info.json"
    external.write_text(
        json.dumps(
            {
                "id": "LinkedVid12",
                "channel_id": "UCexternal",
                "channel": "External",
            }
        ),
        encoding="utf-8",
    )
    source.with_name("Linked [LinkedVid12].info.json").symlink_to(external)

    manifest = scan_corpus(root)

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("transcripts/Linked [LinkedVid12].fr.vtt", "unsafe_channel_metadata")
    ]


def test_scan_corpus_rejects_oversized_flat_channel_metadata(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    source = root / "transcripts" / "Large [LargeVideo1].fr.vtt"
    _write_vtt(source, (0, "large metadata"))
    source.with_name("Large [LargeVideo1].info.json").write_bytes(b" " * (1024 * 1024 + 1))

    manifest = scan_corpus(root)

    assert manifest.documents == ()
    assert [(item.source_relpath, item.reason) for item in manifest.invalid_sources] == [
        ("transcripts/Large [LargeVideo1].fr.vtt", "oversized_channel_metadata")
    ]


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


def test_scan_corpus_with_an_explicit_none_limit_scans_the_full_current_corpus(
    tmp_path: Path,
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    for index in range(51):
        _write_vtt(
            root / "alpha" / "transcripts" / f"Video {index:02d} [Video{index:06d}].fr.vtt",
            (0, f"transcript {index}"),
        )

    manifest = scan_corpus(root, limit=None)

    assert (manifest.sources_discovered, manifest.sources_selected) == (51, 51)
    assert manifest.documents[-1].source_relpath.endswith("Video 50 [Video000050].fr.vtt")


def test_scan_corpus_representative_selection_round_robins_channel_language_groups(
    tmp_path: Path,
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    for channel, language, count in (
        ("alpha", "fr", 80),
        ("beta", "fr", 30),
        ("alpha", "en", 30),
    ):
        for index in range(count):
            _write_vtt(
                root
                / channel
                / "transcripts"
                / f"{channel} {language} {index:03d} [{channel[:1]}{language[:1]}{index:09d}].{language}.vtt",
                (0, "x" * (index + 1)),
            )

    first = scan_corpus(root, limit=50, selection="representative")
    second = scan_corpus(root, limit=50, selection="representative")
    selected_groups = [(document.channel_id, document.language) for document in first.documents]

    assert first == second
    assert first.sources_selected == len(first.documents) == 50
    assert selected_groups[:6] == [
        ("alpha", "en"),
        ("alpha", "fr"),
        ("beta", "fr"),
        ("alpha", "en"),
        ("alpha", "fr"),
        ("beta", "fr"),
    ]
    assert {group: selected_groups.count(group) for group in set(selected_groups)} == {
        ("alpha", "en"): 17,
        ("alpha", "fr"): 17,
        ("beta", "fr"): 16,
    }
    assert [document.source_relpath for document in first.documents] != sorted(
        document.source_relpath for document in first.documents
    )


def test_scan_corpus_representative_selection_varies_source_sizes_within_a_group(
    tmp_path: Path,
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    for index, text_size in enumerate((1, 10, 100, 1_000, 10_000)):
        _write_vtt(
            root / "alpha" / "transcripts" / f"Video {index} [Video{index:06d}].fr.vtt",
            (0, "x" * text_size),
        )

    manifest = scan_corpus(root, limit=4, selection="representative")

    selected_sizes = [
        (root / document.source_relpath).stat().st_size for document in manifest.documents
    ]
    assert selected_sizes == [
        min(selected_sizes),
        max(selected_sizes),
        sorted(selected_sizes)[1],
        sorted(selected_sizes)[-2],
    ]


def test_representative_selection_replaces_an_invalid_source_with_the_next_valid_source(
    tmp_path: Path,
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    _write_vtt(root / "alpha" / "transcripts" / "Broken [BrokenVid12].fr.vtt")
    for index in range(51):
        _write_vtt(
            root / "alpha" / "transcripts" / f"Video {index:02d} [Video{index:06d}].fr.vtt",
            (0, f"transcript {index}"),
        )

    manifest = scan_corpus(root, limit=50, selection="representative")

    assert len(manifest.documents) == 50
    assert manifest.sources_selected == 51
    assert [(source.source_relpath, source.reason) for source in manifest.invalid_sources] == [
        ("alpha/transcripts/Broken [BrokenVid12].fr.vtt", "empty_segments")
    ]


def test_representative_selection_classifies_a_disappeared_source_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import corpus

    root = tmp_path / "output"
    disappeared = root / "alpha" / "transcripts" / "Gone [GoneVideo12].fr.vtt"
    _write_vtt(disappeared, (0, "gone"))
    _write_vtt(
        root / "beta" / "transcripts" / "Kept [KeptVideo12].fr.vtt",
        (0, "kept"),
    )
    original_lstat = Path.lstat

    def lstat_with_disappearance(path: Path):
        if path == disappeared:
            raise FileNotFoundError(path)
        return original_lstat(path)

    monkeypatch.setattr(corpus.Path, "lstat", lstat_with_disappearance)

    manifest = corpus.scan_corpus(root, limit=2, selection="representative")

    assert [document.video_id for document in manifest.documents] == ["KeptVideo12"]
    assert manifest.sources_selected == 2
    assert [(source.source_relpath, source.reason) for source in manifest.invalid_sources] == [
        ("alpha/transcripts/Gone [GoneVideo12].fr.vtt", "source_unavailable")
    ]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO support is unavailable")
def test_scan_corpus_rejects_non_regular_sources_without_blocking(
    request: pytest.FixtureRequest,
) -> None:
    temporary_root = tempfile.TemporaryDirectory(prefix="yt-insights-", dir="/private/tmp")
    request.addfinalizer(temporary_root.cleanup)
    root = Path(temporary_root.name) / "output"
    transcript_dir = root / "alpha" / "transcripts"
    _write_vtt(
        transcript_dir / "Regular [RegularVid1].fr.vtt",
        (0, "regular"),
    )
    (transcript_dir / "Directory [DirectryVid].fr.vtt").mkdir()
    fifo = transcript_dir / "Fifo [FifoVideo12].fr.vtt"
    os.mkfifo(fifo)
    (transcript_dir / "FifoLink [FifoLink123].fr.vtt").symlink_to(fifo)
    device_linked = Path("/dev/null").exists()
    if device_linked:
        (transcript_dir / "Device [DeviceVid12].fr.vtt").symlink_to("/dev/null")
    unix_socket: socket.socket | None = None
    socket_path = transcript_dir / "Socket [SocketVid12].fr.vtt"
    if hasattr(socket, "AF_UNIX"):
        unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            unix_socket.bind(str(socket_path))
        except OSError:
            unix_socket.close()
            unix_socket = None
    script = """
from pathlib import Path
import json
from yt_insights.search.corpus import scan_corpus
manifest = scan_corpus(Path(__import__('sys').argv[1]), limit=None)
print(json.dumps({
    'documents': len(manifest.documents),
    'selected': manifest.sources_selected,
    'invalid': [(item.source_relpath, item.reason) for item in manifest.invalid_sources],
}))
"""
    environment = dict(os.environ, PYTHONPATH=str(Path.cwd() / "src"))

    try:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(root)],
            check=True,
            capture_output=True,
            env=environment,
            text=True,
            timeout=2,
        )
    finally:
        if unix_socket is not None:
            unix_socket.close()
    result = json.loads(completed.stdout)

    assert result["documents"] == 1
    assert result["selected"] == 4 + int(unix_socket is not None) + int(device_linked)
    expected_invalid = [
        ("alpha/transcripts/Device [DeviceVid12].fr.vtt", "outside_corpus_root"),
        ("alpha/transcripts/Directory [DirectryVid].fr.vtt", "non_regular_source"),
        ("alpha/transcripts/Fifo [FifoVideo12].fr.vtt", "non_regular_source"),
        ("alpha/transcripts/FifoLink [FifoLink123].fr.vtt", "symlink_source"),
    ]
    if not device_linked:
        expected_invalid.pop(0)
    if unix_socket is not None:
        expected_invalid.append(
            ("alpha/transcripts/Socket [SocketVid12].fr.vtt", "non_regular_source")
        )
    assert [tuple(item) for item in result["invalid"]] == expected_invalid


def test_scan_corpus_rejects_an_unknown_selection_strategy(tmp_path: Path) -> None:
    from yt_insights.search.corpus import scan_corpus

    with pytest.raises(ValueError, match="selection"):
        scan_corpus(tmp_path, selection="random")


def test_scan_corpus_full_manifest_tracks_add_modify_rename_and_delete_without_ghosts(
    tmp_path: Path,
) -> None:
    from yt_insights.search.corpus import scan_corpus

    root = tmp_path / "output"
    first = root / "alpha" / "transcripts" / "First [FirstVideo1].fr.vtt"
    second = root / "beta" / "transcripts" / "Second [SecondVideo].fr.vtt"
    _write_vtt(first, (0, "first v1"))
    _write_vtt(second, (0, "second"))

    initial = scan_corpus(root, limit=None)
    first_hash = initial.documents[0].source_sha256
    renamed = second.with_name("Renamed [SecondVideo].fr.vtt")
    second.rename(renamed)
    _write_vtt(first, (0, "first v2"))
    added = root / "gamma" / "transcripts" / "Added [AddedVideo1].fr.vtt"
    _write_vtt(added, (0, "added"))

    after_add_modify_rename = scan_corpus(root, limit=None)
    added.unlink()
    after_delete = scan_corpus(root, limit=None)

    assert (
        after_add_modify_rename.sources_discovered
        == after_add_modify_rename.sources_selected
        == 3
    )
    assert {document.source_relpath for document in after_add_modify_rename.documents} == {
        "alpha/transcripts/First [FirstVideo1].fr.vtt",
        "beta/transcripts/Renamed [SecondVideo].fr.vtt",
        "gamma/transcripts/Added [AddedVideo1].fr.vtt",
    }
    updated_first = next(
        document
        for document in after_add_modify_rename.documents
        if document.video_id == "FirstVideo1"
    )
    assert updated_first.source_sha256 != first_hash
    assert after_delete.sources_discovered == after_delete.sources_selected == 2
    assert {document.source_relpath for document in after_delete.documents} == {
        "alpha/transcripts/First [FirstVideo1].fr.vtt",
        "beta/transcripts/Renamed [SecondVideo].fr.vtt",
    }


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
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
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
