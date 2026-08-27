"""Read-only construction of a deterministic local transcript corpus."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from yt_insights.vtt_parser import parse_vtt_timestamped

from .chunker import build_passages
from .models import DocumentRef, Passage, compute_document_id


_FILENAME_RE = re.compile(
    r"^(?:(?:\d{8}) - )?(?P<title>.+?) \[(?P<video_id>[A-Za-z0-9_-]{11})\]\.(?P<language>[A-Za-z0-9-]+)\.vtt$"
)


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


def _relpath(candidate: Path, corpus_root: Path) -> str:
    return candidate.relative_to(corpus_root).as_posix()


def _invalid(source_relpath: str, reason: str) -> InvalidSource:
    return InvalidSource(source_relpath=source_relpath, reason=reason)


def scan_corpus(corpus_root: Path, limit: int | None = None) -> CorpusManifest:
    """Scan up to 50 transcript files and return deterministic in-memory records."""
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50):
        raise ValueError("limit must be an integer from 1 through 50")
    root = Path(corpus_root)
    if not root.is_dir():
        raise ValueError("corpus_root must be an existing directory")

    candidates = sorted(root.glob("**/transcripts/*.vtt"), key=lambda path: _relpath(path, root))
    selected = candidates[: 50 if limit is None else limit]
    resolved_root = root.resolve()
    documents: list[DocumentRef] = []
    passages: list[Passage] = []
    invalid_sources: list[InvalidSource] = []

    for candidate in selected:
        source_relpath = _relpath(candidate, root)
        try:
            candidate.resolve().relative_to(resolved_root)
        except (OSError, ValueError):
            invalid_sources.append(_invalid(source_relpath, "outside_corpus_root"))
            continue

        match = _FILENAME_RE.fullmatch(candidate.name)
        if match is None or not match.group("title").strip():
            invalid_sources.append(_invalid(source_relpath, "unsupported_filename"))
            continue

        try:
            source_bytes = candidate.read_bytes()
        except OSError:
            invalid_sources.append(_invalid(source_relpath, "unreadable_source"))
            continue
        try:
            source_bytes.decode("utf-8")
        except UnicodeDecodeError:
            invalid_sources.append(_invalid(source_relpath, "invalid_utf8"))
            continue

        parse_error = False
        try:
            segments = parse_vtt_timestamped(candidate)
        except (OSError, UnicodeDecodeError, ValueError):
            parse_error = True
            segments = []
        try:
            source_changed = candidate.read_bytes() != source_bytes
        except OSError:
            source_changed = True
        if source_changed:
            invalid_sources.append(_invalid(source_relpath, "source_changed_during_parse"))
            continue
        if parse_error:
            invalid_sources.append(_invalid(source_relpath, "parse_error"))
            continue
        if not segments:
            invalid_sources.append(_invalid(source_relpath, "empty_segments"))
            continue

        channel_slug = candidate.parent.parent.name
        language = match.group("language").lower()
        document = DocumentRef(
            document_id=compute_document_id(channel_slug, match.group("video_id"), language),
            source_relpath=source_relpath,
            source_sha256=sha256(source_bytes).hexdigest(),
            channel_id=channel_slug,
            channel_title=channel_slug,
            video_id=match.group("video_id"),
            video_title=match.group("title").strip(),
            language=language,
        )
        try:
            document_passages = build_passages(document, segments)
        except ValueError:
            invalid_sources.append(_invalid(source_relpath, "invalid_segments"))
            continue
        documents.append(document)
        passages.extend(document_passages)

    return CorpusManifest(
        documents=tuple(documents),
        passages=tuple(passages),
        invalid_sources=tuple(invalid_sources),
        sources_discovered=len(candidates),
        sources_selected=len(selected),
        sources_invalid=len(invalid_sources),
    )
