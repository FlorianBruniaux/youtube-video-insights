from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from yt_insights.cleaner import clean_vtt
from yt_insights.exporter import (
    AmbiguousTranscriptLanguage,
    CorruptTranscriptMetadata,
    DuplicateTranscript,
    ExportTargetExists,
    InvalidVideoReference,
    TranscriptNotFound,
    VideoExportRequest,
    export_video,
    resolve_transcript,
)
from yt_insights.paths import DataPaths


VIDEO_ID = "nfupYzLjFGc"


def _vtt_bytes(text: str = "Bonjour le monde") -> bytes:
    return (
        "WEBVTT\nKind: captions\nLanguage: fr\n\n"
        "00:00:10.000 --> 00:00:12.000\n"
        f"{text}\n\n"
        "00:00:15.000 --> 00:00:17.000\n"
        "Deuxième idée\n"
    ).encode("utf-8")


def _write_transcript(
    root: Path,
    *,
    language: str = "fr",
    video_id: str = VIDEO_ID,
    title: str = "Build reliable agents",
    source: str | None = "Stable Channel",
    flat: bool = False,
    sidecar: object | None = None,
) -> Path:
    transcript_dir = root / "transcripts" if flat else root / "stable-channel" / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    path = transcript_dir / f"{title} [{video_id}].{language}.vtt"
    path.write_bytes(_vtt_bytes())
    if sidecar is not None or flat:
        payload = sidecar
        if payload is None:
            payload = {
                "id": video_id,
                "title": title,
                "channel": source,
                "channel_id": "UCStableChannel123",
            }
        info_path = path.with_name(f"{title} [{video_id}].info.json")
        if isinstance(payload, str):
            info_path.write_text(payload, encoding="utf-8")
        else:
            info_path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "reference",
    (
        VIDEO_ID,
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}&t=10",
        f"https://youtu.be/{VIDEO_ID}",
    ),
)
def test_resolve_transcript_accepts_exact_id_and_supported_urls(
    tmp_path: Path, reference: str
) -> None:
    source = _write_transcript(tmp_path)

    resolved = resolve_transcript(tmp_path, reference, language=None)

    assert resolved.path == source
    assert resolved.video_id == VIDEO_ID
    assert resolved.language == "fr"
    assert resolved.title == "Build reliable agents"
    assert resolved.source == "stable-channel"


@pytest.mark.parametrize(
    "reference",
    (
        "Build reliable agents",
        "nfupYzLjFG",
        f"https://example.com/watch?v={VIDEO_ID}",
        f"https://youtube.com:bad/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}extra",
        f"https://youtu.be/{VIDEO_ID}/extra",
    ),
)
def test_resolve_transcript_rejects_titles_partial_ids_and_non_youtube_urls(
    tmp_path: Path, reference: str
) -> None:
    _write_transcript(tmp_path)

    with pytest.raises(InvalidVideoReference):
        resolve_transcript(tmp_path, reference, language=None)


def test_ambiguous_languages_require_an_explicit_choice(tmp_path: Path) -> None:
    _write_transcript(tmp_path, language="fr")
    english = _write_transcript(tmp_path, language="en")

    with pytest.raises(AmbiguousTranscriptLanguage) as error:
        resolve_transcript(tmp_path, VIDEO_ID, language=None)

    assert error.value.languages == ("en", "fr")
    assert resolve_transcript(tmp_path, VIDEO_ID, language="EN").path == english


def test_missing_requested_language_is_not_silently_substituted(tmp_path: Path) -> None:
    _write_transcript(tmp_path, language="fr")

    with pytest.raises(TranscriptNotFound):
        resolve_transcript(tmp_path, VIDEO_ID, language="en")


def test_flat_inbox_requires_a_valid_adjacent_sidecar(tmp_path: Path) -> None:
    path = _write_transcript(tmp_path, flat=True)
    path.with_name(f"Build reliable agents [{VIDEO_ID}].info.json").unlink()

    with pytest.raises(CorruptTranscriptMetadata):
        resolve_transcript(tmp_path, VIDEO_ID, language="fr")


@pytest.mark.parametrize(
    "sidecar",
    (
        "not-json",
        {"id": "WrongVid123", "channel": "Stable Channel", "title": "Title"},
        {"id": VIDEO_ID, "channel": "", "title": "Title"},
        {"id": VIDEO_ID, "channel": "Stable Channel", "title": ""},
    ),
)
def test_corrupt_adjacent_sidecar_fails_closed(tmp_path: Path, sidecar: object) -> None:
    _write_transcript(tmp_path, flat=True, sidecar=sidecar)

    with pytest.raises(CorruptTranscriptMetadata):
        resolve_transcript(tmp_path, VIDEO_ID, language="fr")


def test_flat_inbox_rejects_a_symlinked_sidecar(tmp_path: Path) -> None:
    source = _write_transcript(tmp_path, flat=True)
    sidecar = source.with_name(f"Build reliable agents [{VIDEO_ID}].info.json")
    sidecar.unlink()
    external = tmp_path / "external.info.json"
    external.write_text(
        json.dumps(
            {"id": VIDEO_ID, "title": "External", "channel": "External channel"}
        ),
        encoding="utf-8",
    )
    sidecar.symlink_to(external)

    with pytest.raises(CorruptTranscriptMetadata):
        resolve_transcript(tmp_path, VIDEO_ID, language="fr")


def test_symlinked_vtt_is_not_exported(tmp_path: Path) -> None:
    external = tmp_path / "outside" / f"Outside [{VIDEO_ID}].fr.vtt"
    external.parent.mkdir()
    external.write_bytes(_vtt_bytes())
    transcripts = tmp_path / "stable-channel" / "transcripts"
    transcripts.mkdir(parents=True)
    (transcripts / f"Linked [{VIDEO_ID}].fr.vtt").symlink_to(external)

    with pytest.raises(TranscriptNotFound):
        resolve_transcript(tmp_path, VIDEO_ID, language="fr")


def test_nested_historical_layout_can_derive_metadata_without_sidecar(tmp_path: Path) -> None:
    source = _write_transcript(tmp_path, source=None)

    resolved = resolve_transcript(tmp_path, VIDEO_ID, language="fr")

    assert resolved.path == source
    assert resolved.title == "Build reliable agents"
    assert resolved.source == "stable-channel"


def test_duplicate_same_video_and_language_fails_closed(tmp_path: Path) -> None:
    _write_transcript(tmp_path / "one", language="fr")
    _write_transcript(tmp_path / "two", language="fr")

    with pytest.raises(DuplicateTranscript) as error:
        resolve_transcript(tmp_path, VIDEO_ID, language="fr")

    assert len(error.value.paths) == 2


def test_missing_vtt_is_reported(tmp_path: Path) -> None:
    with pytest.raises(TranscriptNotFound):
        resolve_transcript(tmp_path, VIDEO_ID, language=None)


def test_vtt_export_is_byte_exact_and_hashes_source_bytes(tmp_path: Path) -> None:
    source = _write_transcript(tmp_path)
    original = b"WEBVTT\r\n\r\n00:00:00.000 --> 00:00:01.000\r\nExact bytes\r\n"
    source.write_bytes(original)
    target = tmp_path / "copy.vtt"

    result = export_video(
        VideoExportRequest(VIDEO_ID, "vtt", "fr", target), DataPaths.from_root(tmp_path)
    )

    assert target.read_bytes() == original
    assert result.source_sha256 == hashlib.sha256(original).hexdigest()
    assert result.path == target.resolve()


def test_text_export_is_normalized_utf8(tmp_path: Path) -> None:
    source = _write_transcript(tmp_path)
    target = tmp_path / "source.txt"

    export_video(
        VideoExportRequest(VIDEO_ID, "txt", "fr", target), DataPaths.from_root(tmp_path)
    )

    assert target.read_bytes() == f"{clean_vtt(source)}\n".encode("utf-8")


def test_markdown_export_keeps_provenance_and_timestamps(tmp_path: Path) -> None:
    source = _write_transcript(tmp_path, flat=True)
    target = tmp_path / "source.md"

    result = export_video(
        VideoExportRequest(VIDEO_ID, "md", "fr", target), DataPaths.from_root(tmp_path)
    )
    body = result.path.read_text(encoding="utf-8")

    assert "# Build reliable agents" in body
    assert "Stable Channel" in body
    assert f"https://www.youtube.com/watch?v={VIDEO_ID}" in body
    assert "[00:00:10] Bonjour le monde" in body
    assert "[00:00:15] Deuxième idée" in body
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_default_target_uses_exports_directory_and_stable_name(tmp_path: Path) -> None:
    _write_transcript(tmp_path)
    paths = DataPaths.from_root(tmp_path)

    result = export_video(VideoExportRequest(VIDEO_ID, "md", "fr"), paths)

    assert result.path == (paths.exports / f"{VIDEO_ID}.fr.md").resolve()


def test_existing_target_requires_force(tmp_path: Path) -> None:
    _write_transcript(tmp_path)
    target = tmp_path / "source.md"
    target.write_text("keep me", encoding="utf-8")

    with pytest.raises(ExportTargetExists):
        export_video(
            VideoExportRequest(VIDEO_ID, "md", "fr", target), DataPaths.from_root(tmp_path)
        )
    assert target.read_text(encoding="utf-8") == "keep me"

    export_video(
        VideoExportRequest(VIDEO_ID, "md", "fr", target, force=True),
        DataPaths.from_root(tmp_path),
    )
    assert target.read_text(encoding="utf-8").startswith("# Build reliable agents")
    assert not target.with_name(f"{target.name}.tmp").exists()
