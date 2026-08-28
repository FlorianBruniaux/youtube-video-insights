"""Deterministic, source-backed transcript exports without an LLM call."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import parse_qs, urlsplit

from .cleaner import clean_vtt
from .paths import DataPaths
from .vtt_parser import parse_vtt_timestamped, seconds_to_hms


_VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_VTT_NAME_RE = re.compile(
    r"^(?:(?:\d{8}) - )?(?P<title>.+?) \[(?P<video_id>[A-Za-z0-9_-]{11})\]\."
    r"(?P<language>[A-Za-z0-9-]+)\.vtt$"
)
_MAX_SIDECAR_BYTES = 1024 * 1024


class ExportError(Exception):
    """Base class for safe, user-facing export failures."""


class InvalidVideoReference(ExportError):
    """The input is neither an exact video ID nor a supported YouTube URL."""


class TranscriptNotFound(ExportError):
    """No transcript matched the exact video ID and optional language."""


class AmbiguousTranscriptLanguage(ExportError):
    """More than one language exists and the caller did not choose one."""

    def __init__(self, languages: tuple[str, ...]) -> None:
        self.languages = languages
        super().__init__(f"multiple transcript languages found: {', '.join(languages)}")


class DuplicateTranscript(ExportError):
    """More than one source claims the same exact video ID and language."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self.paths = paths
        super().__init__("multiple transcripts claim the same video ID and language")


class CorruptTranscriptMetadata(ExportError):
    """Required adjacent yt-dlp metadata is missing, unsafe or invalid."""


class ExportTargetExists(ExportError):
    """The requested output path already exists and force was not set."""


class SourceChangedDuringExport(ExportError):
    """The source changed while it was being rendered."""


@dataclass(frozen=True, slots=True)
class ResolvedTranscript:
    path: Path
    video_id: str
    language: str
    title: str
    source: str

    @property
    def canonical_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass(frozen=True, slots=True)
class VideoExportRequest:
    video_or_url: str
    format: str
    language: str | None = None
    output: Path | None = None
    force: bool = False


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: Path
    source_sha256: str
    video_id: str
    language: str
    format: str

    def to_dict(self) -> dict[str, str]:
        return {
            "format": self.format,
            "language": self.language,
            "path": str(self.path),
            "source_sha256": self.source_sha256,
            "video_id": self.video_id,
        }


def parse_video_id(video_or_url: str) -> str:
    """Return an exact YouTube video ID from the supported input forms."""
    reference = video_or_url.strip()
    if _VIDEO_ID_RE.fullmatch(reference):
        return reference

    try:
        parsed = urlsplit(reference)
        port = parsed.port
    except ValueError as error:
        raise InvalidVideoReference("invalid YouTube video reference") from error
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or port:
        raise InvalidVideoReference("expected an exact video ID or YouTube URL")

    host = (parsed.hostname or "").lower().rstrip(".")
    video_id: str | None = None
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parsed.path == "/watch":
        values = parse_qs(parsed.query, keep_blank_values=True).get("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif host == "youtu.be" and parsed.path.count("/") == 1:
        video_id = parsed.path.removeprefix("/")

    if video_id is None or _VIDEO_ID_RE.fullmatch(video_id) is None:
        raise InvalidVideoReference("expected an exact video ID or YouTube URL")
    return video_id


def _sidecar_path(path: Path, language: str) -> Path:
    stem = path.name.removesuffix(f".{language}.vtt")
    return path.with_name(f"{stem}.info.json")


def _safe_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _read_sidecar(path: Path, video_id: str) -> tuple[str, str]:
    try:
        details = path.lstat()
    except OSError as error:
        raise CorruptTranscriptMetadata(f"missing or unreadable transcript metadata: {path.name}") from error
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise CorruptTranscriptMetadata(f"unsafe transcript metadata: {path.name}")
    if details.st_size > _MAX_SIDECAR_BYTES:
        raise CorruptTranscriptMetadata(f"oversized transcript metadata: {path.name}")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise CorruptTranscriptMetadata(f"unsafe transcript metadata: {path.name}")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
            or opened.st_size > _MAX_SIDECAR_BYTES
        ):
            raise CorruptTranscriptMetadata(f"unsafe transcript metadata: {path.name}")
        chunks: list[bytes] = []
        remaining = _MAX_SIDECAR_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        encoded = b"".join(chunks)
        final = os.fstat(descriptor)
        if (
            len(encoded) > _MAX_SIDECAR_BYTES
            or (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise CorruptTranscriptMetadata(f"unsafe transcript metadata: {path.name}")
    except OSError as error:
        raise CorruptTranscriptMetadata(f"invalid transcript metadata: {path.name}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorruptTranscriptMetadata(f"invalid transcript metadata: {path.name}") from error
    if not isinstance(payload, dict) or payload.get("id") != video_id:
        raise CorruptTranscriptMetadata(f"invalid transcript metadata: {path.name}")
    title = _safe_text(payload.get("title"))
    source = _safe_text(payload.get("channel")) or _safe_text(payload.get("uploader"))
    if title is None or source is None:
        raise CorruptTranscriptMetadata(f"invalid transcript metadata: {path.name}")
    return title, source


def _candidate_from_path(path: Path, root: Path, video_id: str) -> ResolvedTranscript | None:
    match = _VTT_NAME_RE.fullmatch(path.name)
    if match is None or match.group("video_id") != video_id:
        return None
    try:
        details = path.lstat()
        path.resolve().relative_to(root)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode):
        return None

    language = match.group("language").lower()
    sidecar = _sidecar_path(path, match.group("language"))
    flat_inbox = path.parent == root / "transcripts"
    if flat_inbox or sidecar.exists() or sidecar.is_symlink():
        title, source = _read_sidecar(sidecar, video_id)
    else:
        title = match.group("title").strip()
        source = path.parent.parent.name.strip()
        if not title or not source:
            raise CorruptTranscriptMetadata(f"cannot derive transcript metadata: {path.name}")
    return ResolvedTranscript(
        path=path,
        video_id=video_id,
        language=language,
        title=title,
        source=source,
    )


def resolve_transcript(
    corpus_root: Path, video_or_url: str, language: str | None
) -> ResolvedTranscript:
    """Resolve exactly one source from the filesystem corpus, failing closed."""
    video_id = parse_video_id(video_or_url)
    root = Path(corpus_root).expanduser().resolve(strict=False)
    requested_language = language.strip().lower() if language is not None else None
    if requested_language == "":
        raise TranscriptNotFound("transcript language must not be empty")

    candidates: list[ResolvedTranscript] = []
    for path in sorted(root.glob("**/transcripts/*.vtt")):
        candidate = _candidate_from_path(path, root, video_id)
        if candidate is not None:
            candidates.append(candidate)

    if requested_language is not None:
        candidates = [item for item in candidates if item.language == requested_language]
    if not candidates:
        suffix = f" in language {requested_language}" if requested_language else ""
        raise TranscriptNotFound(f"no transcript found for video {video_id}{suffix}")

    by_language: dict[str, list[ResolvedTranscript]] = {}
    for candidate in candidates:
        by_language.setdefault(candidate.language, []).append(candidate)
    duplicate_paths = [
        tuple(sorted((item.path for item in group), key=str))
        for group in by_language.values()
        if len(group) > 1
    ]
    if duplicate_paths:
        raise DuplicateTranscript(tuple(path for group in duplicate_paths for path in group))
    if requested_language is None and len(by_language) > 1:
        raise AmbiguousTranscriptLanguage(tuple(sorted(by_language)))
    return candidates[0]


def _markdown(resolved: ResolvedTranscript, source_hash: str) -> str:
    segments = parse_vtt_timestamped(resolved.path)
    transcript = "\n\n".join(
        f"[{seconds_to_hms(segment['start'])}] {segment['text']}" for segment in segments
    )
    return (
        f"# {resolved.title}\n\n"
        f"- Source: {resolved.source}\n"
        f"- Video ID: {resolved.video_id}\n"
        f"- Language: {resolved.language}\n"
        f"- URL: {resolved.canonical_url}\n"
        f"- Source SHA-256: {source_hash}\n\n"
        f"## Transcript\n\n{transcript}\n"
    )


def _target_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _write_atomic(target: Path, payload: bytes, *, force: bool) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not force and _target_exists(target):
        raise ExportTargetExists(f"export target already exists: {target}")
    temporary = target.with_name(f"{target.name}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not force and _target_exists(target):
            raise ExportTargetExists(f"export target already exists: {target}")
        os.replace(temporary, target)
    except FileExistsError as error:
        raise ExportError(f"temporary export path already exists: {temporary}") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def export_video(request: VideoExportRequest, paths: DataPaths) -> ExportResult:
    """Export one transcript to VTT, normalized text or provenance Markdown."""
    output_format = request.format.strip().lower()
    if output_format not in {"vtt", "txt", "md"}:
        raise ExportError(f"unsupported export format: {request.format}")
    resolved = resolve_transcript(paths.root, request.video_or_url, request.language)
    source_bytes = resolved.path.read_bytes()
    source_hash = sha256(source_bytes).hexdigest()

    if output_format == "vtt":
        payload = source_bytes
    elif output_format == "txt":
        payload = f"{clean_vtt(resolved.path)}\n".encode("utf-8")
    else:
        payload = _markdown(resolved, source_hash).encode("utf-8")
    if resolved.path.read_bytes() != source_bytes:
        raise SourceChangedDuringExport("transcript changed during export")

    target = request.output or paths.exports / f"{resolved.video_id}.{resolved.language}.{output_format}"
    absolute_target = Path(target).expanduser().resolve(strict=False)
    _write_atomic(absolute_target, payload, force=request.force)
    return ExportResult(
        path=absolute_target,
        source_sha256=source_hash,
        video_id=resolved.video_id,
        language=resolved.language,
        format=output_format,
    )
