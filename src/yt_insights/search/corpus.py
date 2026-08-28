"""Read-only construction of a deterministic local transcript corpus."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from yt_insights.vtt_parser import parse_vtt_timestamped

from .chunker import build_passages
from .models import DocumentRef, Passage, compute_document_id


_FILENAME_RE = re.compile(
    r"^(?:(?:\d{8}) - )?(?P<title>.+?) \[(?P<video_id>[A-Za-z0-9_-]{11})\]\.(?P<language>[A-Za-z0-9-]+)\.vtt$"
)
_CHANNEL_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,200}")
_MAX_INFO_JSON_BYTES = 1024 * 1024
_MAX_CHANNEL_TITLE_CODEPOINTS = 500


@dataclass(frozen=True, slots=True)
class InvalidSource:
    source_relpath: str
    reason: str


@dataclass(frozen=True, slots=True)
class CorpusManifest:
    documents: tuple[DocumentRef, ...]
    passages: tuple[Passage, ...]
    invalid_sources: tuple[InvalidSource, ...]
    sources_discovered: int
    sources_selected: int
    sources_invalid: int


@dataclass(frozen=True, slots=True)
class _CorpusSource:
    path: Path
    source_relpath: str
    channel_slug: str
    channel_title: str
    language: str | None
    size: int | None
    device: int | None
    inode: int | None
    invalid_reason: str | None


def _relpath(candidate: Path, corpus_root: Path) -> str:
    return candidate.relative_to(corpus_root).as_posix()


def _invalid(source_relpath: str, reason: str) -> InvalidSource:
    return InvalidSource(source_relpath=source_relpath, reason=reason)


def _channel_title(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_CHANNEL_TITLE_CODEPOINTS
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    return normalized


def _read_bounded_info_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        path_details = path.lstat()
    except FileNotFoundError:
        return None, "missing_channel_metadata"
    except OSError:
        return None, "unsafe_channel_metadata"
    if not stat.S_ISREG(path_details.st_mode):
        return None, "unsafe_channel_metadata"
    if path_details.st_size > _MAX_INFO_JSON_BYTES:
        return None, "oversized_channel_metadata"

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        return None, "unsafe_channel_metadata"
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (path_details.st_dev, path_details.st_ino)
            or opened.st_size > _MAX_INFO_JSON_BYTES
        ):
            return None, "unsafe_channel_metadata"
        chunks: list[bytes] = []
        remaining = _MAX_INFO_JSON_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            len(encoded) > _MAX_INFO_JSON_BYTES
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            return None, "unsafe_channel_metadata"
    except OSError:
        return None, "unsafe_channel_metadata"
    finally:
        if descriptor is not None:
            os.close(descriptor)

    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid_channel_metadata"
    if not isinstance(payload, dict):
        return None, "invalid_channel_metadata"
    return payload, None


def _flat_channel_metadata(
    candidate: Path, *, language: str, video_id: str
) -> tuple[str, str, str | None]:
    info_name = candidate.name.removesuffix(f".{language}.vtt") + ".info.json"
    payload, error = _read_bounded_info_json(candidate.with_name(info_name))
    if error is not None or payload is None:
        return "", "", error
    channel_id = payload.get("channel_id")
    raw_channel_title = payload.get("channel")
    if raw_channel_title is None:
        raw_channel_title = payload.get("uploader")
    channel_title = _channel_title(raw_channel_title)
    if (
        payload.get("id") != video_id
        or not isinstance(channel_id, str)
        or _CHANNEL_ID_RE.fullmatch(channel_id) is None
        or channel_title is None
    ):
        return "", "", "invalid_channel_metadata"
    return channel_id, channel_title, None


def _inventory_source(candidate: Path, corpus_root: Path, resolved_root: Path) -> _CorpusSource:
    source_relpath = _relpath(candidate, corpus_root)
    legacy_channel_slug = candidate.parent.parent.name
    try:
        source_stat = candidate.lstat()
    except OSError:
        return _CorpusSource(
            candidate, source_relpath, "", "", None, None, None, None, "source_unavailable"
        )
    try:
        candidate.resolve().relative_to(resolved_root)
    except OSError:
        return _CorpusSource(
            candidate, source_relpath, "", "", None, None, None, None, "source_unavailable"
        )
    except ValueError:
        return _CorpusSource(
            candidate, source_relpath, "", "", None, None, None, None, "outside_corpus_root"
        )
    if stat.S_ISLNK(source_stat.st_mode):
        return _CorpusSource(
            candidate, source_relpath, "", "", None, None, None, None, "symlink_source"
        )
    if not stat.S_ISREG(source_stat.st_mode):
        return _CorpusSource(
            candidate, source_relpath, "", "", None, None, None, None, "non_regular_source"
        )
    match = _FILENAME_RE.fullmatch(candidate.name)
    language = match.group("language").lower() if match is not None else None
    invalid_reason = None if match is not None and match.group("title").strip() else "unsupported_filename"
    channel_slug = legacy_channel_slug
    channel_title = legacy_channel_slug
    if invalid_reason is None and candidate.parent == corpus_root / "transcripts":
        assert match is not None and language is not None
        channel_slug, channel_title, invalid_reason = _flat_channel_metadata(
            candidate,
            language=match.group("language"),
            video_id=match.group("video_id"),
        )
    return _CorpusSource(
        candidate,
        source_relpath,
        channel_slug,
        channel_title,
        language,
        source_stat.st_size,
        source_stat.st_dev,
        source_stat.st_ino,
        invalid_reason,
    )


def discover_corpus_sources(corpus_root: Path) -> tuple[_CorpusSource, ...]:
    """Inventory transcript-path candidates without opening their content."""
    root = Path(corpus_root)
    resolved_root = root.resolve()
    candidates = sorted(
        root.glob("**/transcripts/*.vtt"), key=lambda candidate: _relpath(candidate, root)
    )
    return tuple(_inventory_source(candidate, root, resolved_root) for candidate in candidates)


def _size_diverse_candidates(candidates: list[_CorpusSource]) -> deque[_CorpusSource]:
    by_size = sorted(
        candidates,
        key=lambda candidate: (candidate.size, candidate.source_relpath),
    )
    diverse: deque[_CorpusSource] = deque()
    low = 0
    high = len(by_size) - 1
    while low <= high:
        diverse.append(by_size[low])
        low += 1
        if low <= high:
            diverse.append(by_size[high])
            high -= 1
    return diverse


def _representative_candidates(
    candidates: tuple[_CorpusSource, ...]
) -> tuple[_CorpusSource, ...]:
    grouped: dict[tuple[str, str], list[_CorpusSource]] = {}
    deferred_invalid: list[_CorpusSource] = []
    for candidate in candidates:
        if candidate.invalid_reason is not None:
            deferred_invalid.append(candidate)
            continue
        assert candidate.language is not None
        grouped.setdefault((candidate.channel_slug, candidate.language), []).append(candidate)
    queues = {
        group: _size_diverse_candidates(group_candidates)
        for group, group_candidates in grouped.items()
    }
    selected: list[_CorpusSource] = []
    active_groups = sorted(queues)
    while active_groups:
        next_active_groups: list[tuple[str, str]] = []
        for group in active_groups:
            queue = queues[group]
            selected.append(queue.popleft())
            if queue:
                next_active_groups.append(group)
        active_groups = next_active_groups
    selected.extend(sorted(deferred_invalid, key=lambda candidate: candidate.source_relpath))
    return tuple(selected)


class _UnstableSourceError(OSError):
    """A source changed identity after the no-follow inventory check."""


def _open_stable_source(candidate: _CorpusSource) -> int:
    if candidate.device is None or candidate.inode is None:
        raise _UnstableSourceError("source was not successfully inventoried")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise _UnstableSourceError("platform does not support no-follow source reads")
    descriptor = os.open(candidate.path, os.O_RDONLY | os.O_NONBLOCK | no_follow)
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_dev != candidate.device
            or current.st_ino != candidate.inode
        ):
            raise _UnstableSourceError("source changed after inventory")
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _read_stable_source(candidate: _CorpusSource) -> bytes:
    descriptor = _open_stable_source(candidate)
    try:
        with os.fdopen(descriptor, "rb") as source_file:
            return source_file.read()
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise


def _stable_file_size(candidate: _CorpusSource) -> int:
    descriptor = _open_stable_source(candidate)
    try:
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)


def _parse_source_bytes(source_bytes: bytes) -> list[dict]:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary_source = Path(temporary_directory) / "source.vtt"
        temporary_source.write_bytes(source_bytes)
        return parse_vtt_timestamped(temporary_source)


def scan_corpus(
    corpus_root: Path, limit: int | None = 50, selection: str = "ordered"
) -> CorpusManifest:
    """Scan a deterministic transcript slice, or the full corpus when ``limit`` is None."""
    if limit is not None and (
        isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50
    ):
        raise ValueError("limit must be an integer from 1 through 50")
    if selection not in {"ordered", "representative"}:
        raise ValueError("selection must be 'ordered' or 'representative'")
    root = Path(corpus_root)
    if not root.is_dir():
        raise ValueError("corpus_root must be an existing directory")

    candidates = discover_corpus_sources(root)
    if limit is None:
        selected = candidates
    elif selection == "ordered":
        selected = candidates[:limit]
    else:
        selected = _representative_candidates(candidates)
    documents: list[DocumentRef] = []
    passages: list[Passage] = []
    invalid_sources: list[InvalidSource] = []

    for candidate in selected:
        if limit is not None and selection == "representative" and len(documents) == limit:
            break
        if candidate.invalid_reason is not None:
            invalid_sources.append(_invalid(candidate.source_relpath, candidate.invalid_reason))
            continue

        try:
            source_bytes = _read_stable_source(candidate)
        except (OSError, _UnstableSourceError):
            invalid_sources.append(_invalid(candidate.source_relpath, "unreadable_source"))
            continue
        try:
            source_bytes.decode("utf-8")
        except UnicodeDecodeError:
            invalid_sources.append(_invalid(candidate.source_relpath, "invalid_utf8"))
            continue

        parse_error = False
        try:
            segments = _parse_source_bytes(source_bytes)
        except (OSError, UnicodeDecodeError, ValueError):
            parse_error = True
            segments = []
        try:
            source_changed = _read_stable_source(candidate) != source_bytes
        except (OSError, _UnstableSourceError):
            source_changed = True
        if source_changed:
            invalid_sources.append(_invalid(candidate.source_relpath, "source_changed_during_parse"))
            continue
        if parse_error:
            invalid_sources.append(_invalid(candidate.source_relpath, "parse_error"))
            continue
        if not segments:
            invalid_sources.append(_invalid(candidate.source_relpath, "empty_segments"))
            continue

        assert candidate.language is not None
        match = _FILENAME_RE.fullmatch(candidate.path.name)
        assert match is not None
        channel_slug = candidate.channel_slug
        language = candidate.language
        document = DocumentRef(
            document_id=compute_document_id(channel_slug, match.group("video_id"), language),
            source_relpath=candidate.source_relpath,
            source_sha256=sha256(source_bytes).hexdigest(),
            channel_id=channel_slug,
            channel_title=candidate.channel_title,
            video_id=match.group("video_id"),
            video_title=match.group("title").strip(),
            language=language,
        )
        try:
            document_passages = build_passages(document, segments)
        except ValueError:
            invalid_sources.append(_invalid(candidate.source_relpath, "invalid_segments"))
            continue
        documents.append(document)
        passages.extend(document_passages)

    return CorpusManifest(
        documents=tuple(documents),
        passages=tuple(passages),
        invalid_sources=tuple(invalid_sources),
        sources_discovered=len(candidates),
        sources_selected=len(documents) + len(invalid_sources),
        sources_invalid=len(invalid_sources),
    )
