"""Immutable public domain records for local transcript search."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from pathlib import PurePosixPath

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_URL_RE = re.compile(r"https://youtube\.com/watch\?v=([A-Za-z0-9_-]{11})&t=(\d+)s")


def _require_nonblank(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_no_nul(name: str, value: str) -> None:
    if "\0" in value:
        raise ValueError(f"{name} must not contain a NUL byte")


def _require_sha256(name: str, value: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def _require_video_id(video_id: str) -> None:
    if not isinstance(video_id, str) or _VIDEO_ID_RE.fullmatch(video_id) is None:
        raise ValueError("video_id must be an 11-character YouTube video ID")


def _require_nonnegative_int(name: str, value: int, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")


def _require_timestamp(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")


def compute_document_id(channel_id: str, video_id: str, language: str) -> str:
    """Return the deterministic identity of one channel/video/language document."""
    _require_nonblank("channel_id", channel_id)
    _require_no_nul("channel_id", channel_id)
    _require_video_id(video_id)
    _require_nonblank("language", language)
    _require_no_nul("language", language)
    return sha256(f"{channel_id}\0{video_id}\0{language}".encode()).hexdigest()


def compute_passage_id(
    document_id: str,
    ordinal: int,
    start_seconds: float,
    end_seconds: float,
    text: str,
) -> str:
    """Return the deterministic identity of a passage within a document."""
    _require_sha256("document_id", document_id)
    _require_nonnegative_int("ordinal", ordinal)
    _require_timestamp("start_seconds", start_seconds)
    _require_timestamp("end_seconds", end_seconds)
    if end_seconds < start_seconds:
        raise ValueError("end_seconds must be greater than or equal to start_seconds")
    _require_nonblank("text", text)

    normalized_text = " ".join(text.split())
    text_digest = sha256(normalized_text.encode("utf-8")).hexdigest()
    payload = "\0".join(
        (
            document_id,
            str(ordinal),
            str(round(start_seconds * 1000)),
            str(round(end_seconds * 1000)),
            text_digest,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def youtube_url(video_id: str, start_seconds: float) -> str:
    """Return the canonical timestamped YouTube URL for a passage start."""
    _require_video_id(video_id)
    _require_timestamp("start_seconds", start_seconds)
    return f"https://youtube.com/watch?v={video_id}&t={int(start_seconds)}s"


@dataclass(frozen=True, slots=True)
class DocumentRef:
    document_id: str
    source_relpath: str
    source_sha256: str
    channel_id: str
    channel_title: str
    video_id: str
    video_title: str
    language: str

    def __post_init__(self) -> None:
        _require_sha256("document_id", self.document_id)
        _require_nonblank("source_relpath", self.source_relpath)
        source_path = PurePosixPath(self.source_relpath)
        if "\\" in self.source_relpath or source_path.is_absolute() or ".." in source_path.parts:
            raise ValueError("source_relpath must be a POSIX relative path without '..'")
        _require_sha256("source_sha256", self.source_sha256)
        _require_nonblank("channel_id", self.channel_id)
        _require_nonblank("channel_title", self.channel_title)
        _require_video_id(self.video_id)
        _require_nonblank("video_title", self.video_title)
        _require_nonblank("language", self.language)
        if self.document_id != compute_document_id(
            self.channel_id, self.video_id, self.language
        ):
            raise ValueError("document_id does not match the document identity")


@dataclass(frozen=True, slots=True)
class Passage:
    passage_id: str
    document_id: str
    ordinal: int
    start_seconds: float
    end_seconds: float
    text: str
    youtube_url: str

    def __post_init__(self) -> None:
        _require_sha256("passage_id", self.passage_id)
        _require_sha256("document_id", self.document_id)
        _require_nonnegative_int("ordinal", self.ordinal)
        _require_timestamp("start_seconds", self.start_seconds)
        _require_timestamp("end_seconds", self.end_seconds)
        if self.end_seconds < self.start_seconds:
            raise ValueError("end_seconds must be greater than or equal to start_seconds")
        _require_nonblank("text", self.text)
        _require_nonblank("youtube_url", self.youtube_url)
        url_match = _YOUTUBE_URL_RE.fullmatch(self.youtube_url)
        if url_match is None or url_match.group(2) != str(int(self.start_seconds)):
            raise ValueError("youtube_url must be a canonical timestamped YouTube URL")
        if self.passage_id != compute_passage_id(
            self.document_id,
            self.ordinal,
            self.start_seconds,
            self.end_seconds,
            self.text,
        ):
            raise ValueError("passage_id does not match the passage identity")


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    channel: str | None = None
    language: str | None = None
    limit: int = 10

    def __post_init__(self) -> None:
        _require_nonblank("text", self.text)
        if self.channel is not None:
            _require_nonblank("channel", self.channel)
        if self.language is not None:
            _require_nonblank("language", self.language)
        _require_nonnegative_int("limit", self.limit, minimum=1)
        if self.limit > 20:
            raise ValueError("limit must be less than or equal to 20")


@dataclass(frozen=True, slots=True)
class SearchHit:
    document: DocumentRef
    passage: Passage
    rank: int
    score: float
    excerpt: str | None = None

    def __post_init__(self) -> None:
        if self.passage.document_id != self.document.document_id:
            raise ValueError("passage document_id must match document")
        if self.passage.youtube_url != youtube_url(
            self.document.video_id, self.passage.start_seconds
        ):
            raise ValueError("passage youtube_url must match document video_id and start_seconds")
        _require_nonnegative_int("rank", self.rank, minimum=1)
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not isfinite(self.score)
        ):
            raise ValueError("score must be finite")
        if self.excerpt is None:
            object.__setattr__(self, "excerpt", self.passage.text)
        else:
            _require_nonblank("excerpt", self.excerpt)


@dataclass(frozen=True, slots=True)
class BuildReport:
    sources_discovered: int
    sources_selected: int
    sources_invalid: int
    documents_indexed: int
    passages_indexed: int
    invalid_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "sources_discovered",
            "sources_selected",
            "sources_invalid",
            "documents_indexed",
            "passages_indexed",
        ):
            _require_nonnegative_int(name, getattr(self, name))
        if self.sources_selected + self.sources_invalid > self.sources_discovered:
            raise ValueError("sources_selected and sources_invalid exceed sources_discovered")
        if not isinstance(self.invalid_sources, tuple):
            raise ValueError("invalid_sources must be a tuple")
