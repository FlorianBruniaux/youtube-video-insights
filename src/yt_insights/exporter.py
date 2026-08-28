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


class UnsafeTranscriptSource(ExportError):
    """The selected transcript is no longer the validated regular file."""


class UnsafeExportTarget(ExportError):
    """The output path or default export directory is unsafe."""


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


@dataclass(frozen=True, slots=True)
class _VttSnapshot:
    """Path-like text reader backed by one validated source byte snapshot."""

    contents: bytes

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.contents.decode(encoding)


@dataclass(frozen=True, slots=True)
class _DirectoryStep:
    parent_fd: int
    name: str
    directory_fd: int
    identity: tuple[int, int]


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
    except (NotImplementedError, OSError) as error:
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


def _read_source_snapshot(path: Path) -> bytes:
    """Read one regular file descriptor and reject pathname replacement races."""
    try:
        before = path.lstat()
    except OSError as error:
        raise UnsafeTranscriptSource("transcript source is unavailable") from error
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise UnsafeTranscriptSource("transcript source is not a regular file")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise UnsafeTranscriptSource("this platform cannot safely open transcript sources")

    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | no_follow)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise UnsafeTranscriptSource("transcript source changed before it was opened")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final = os.fstat(descriptor)
        if (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise UnsafeTranscriptSource("transcript source changed while it was read")
        try:
            after = path.lstat()
        except OSError as error:
            raise UnsafeTranscriptSource("transcript source path changed while it was read") from error
        if (
            not stat.S_ISREG(after.st_mode)
            or stat.S_ISLNK(after.st_mode)
            or (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise UnsafeTranscriptSource("transcript source path changed while it was read")
        return b"".join(chunks)
    except (NotImplementedError, OSError) as error:
        raise UnsafeTranscriptSource("transcript source could not be read safely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _markdown(
    resolved: ResolvedTranscript, source_hash: str, snapshot: _VttSnapshot
) -> str:
    segments = parse_vtt_timestamped(snapshot)  # type: ignore[arg-type]
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


def _absolute_without_following_symlinks(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _normalize_configured_exports(path: Path) -> Path:
    """Normalize parent aliases while preserving the final component for no-follow checks."""
    lexical = _absolute_without_following_symlinks(path)
    if lexical.name in {"", ".", ".."}:
        raise UnsafeExportTarget("configured exports directory needs a final directory name")
    try:
        parent = lexical.parent.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise UnsafeExportTarget("configured exports directory could not be normalized safely") from error
    return parent / lexical.name


def _filesystem_anchor(path: Path) -> Path:
    anchor = Path(path.anchor)
    if not anchor.is_absolute():
        raise UnsafeExportTarget("configured exports directory has no stable filesystem anchor")
    return anchor


def _validate_existing_target(target: Path, *, force: bool) -> None:
    try:
        details = target.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise UnsafeExportTarget("export target could not be inspected safely") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise UnsafeExportTarget("export target must not be a symlink or special file")
    if not force:
        raise ExportTargetExists(f"export target already exists: {target}")


def _validate_target_at(directory_fd: int, filename: str, *, force: bool) -> None:
    try:
        details = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except (NotImplementedError, OSError) as error:
        raise UnsafeExportTarget("export target could not be inspected safely") from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise UnsafeExportTarget("export target must not be a symlink or special file")
    if not force:
        raise ExportTargetExists(f"export target already exists: {filename}")


def _open_validated_directory(directory: Path) -> tuple[int, tuple[int, int]]:
    try:
        before = directory.lstat()
    except OSError as error:
        raise UnsafeExportTarget("filesystem anchor is unavailable") from error
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise UnsafeExportTarget("filesystem anchor must not be a symlink")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_only = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory_only:
        raise UnsafeExportTarget("this platform cannot safely open the filesystem anchor")
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY | no_follow | directory_only)
        opened = os.fstat(descriptor)
    except (NotImplementedError, OSError) as error:
        if descriptor is not None:
            os.close(descriptor)
        raise UnsafeExportTarget("filesystem anchor could not be opened safely") from error
    assert descriptor is not None
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(descriptor)
        raise UnsafeExportTarget("filesystem anchor changed before it was opened")
    return descriptor, (opened.st_dev, opened.st_ino)


def _directory_path_matches(directory: Path, identity: tuple[int, int]) -> bool:
    try:
        details = directory.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(details.st_mode)
        and not stat.S_ISLNK(details.st_mode)
        and (details.st_dev, details.st_ino) == identity
    )


def _exports_relative_parts(anchor: Path, exports: Path) -> tuple[str, ...]:
    try:
        relative = exports.relative_to(anchor)
    except ValueError as error:
        raise UnsafeExportTarget("configured exports directory escaped its filesystem anchor") from error
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} or Path(part).name != part for part in parts):
        raise UnsafeExportTarget("default exports directory is not a safe relative path")
    return parts


def _open_directory_at(parent_fd: int, name: str) -> tuple[int, tuple[int, int]]:
    if Path(name).name != name or name in {"", ".", ".."}:
        raise UnsafeExportTarget("unsafe exports directory component")
    try:
        details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except (NotImplementedError, OSError) as error:
            raise UnsafeExportTarget("exports directory could not be created safely") from error
        try:
            details = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except (NotImplementedError, OSError) as error:
            raise UnsafeExportTarget("exports directory could not be inspected safely") from error
    except (NotImplementedError, OSError) as error:
        raise UnsafeExportTarget("exports directory could not be inspected safely") from error
    if not stat.S_ISDIR(details.st_mode) or stat.S_ISLNK(details.st_mode):
        raise UnsafeExportTarget("exports directory components must not be symlinks")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_only = getattr(os, "O_DIRECTORY", 0)
    if not no_follow or not directory_only:
        raise UnsafeExportTarget("this platform cannot safely traverse the exports directory")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | no_follow | directory_only,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
    except (NotImplementedError, OSError) as error:
        if descriptor is not None:
            os.close(descriptor)
        raise UnsafeExportTarget("exports directory could not be opened safely") from error
    assert descriptor is not None
    if (
        not stat.S_ISDIR(opened.st_mode)
        or (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino)
    ):
        os.close(descriptor)
        raise UnsafeExportTarget("exports directory changed before it was opened")
    return descriptor, (opened.st_dev, opened.st_ino)


def _open_exports_from_root(root_fd: int, parts: tuple[str, ...]) -> list[_DirectoryStep]:
    steps: list[_DirectoryStep] = []
    parent_fd = root_fd
    try:
        for name in parts:
            directory_fd, identity = _open_directory_at(parent_fd, name)
            steps.append(_DirectoryStep(parent_fd, name, directory_fd, identity))
            parent_fd = directory_fd
    except ExportError:
        for step in reversed(steps):
            os.close(step.directory_fd)
        raise
    return steps


def _directory_steps_match(steps: list[_DirectoryStep]) -> bool:
    for step in steps:
        try:
            details = os.stat(step.name, dir_fd=step.parent_fd, follow_symlinks=False)
        except (NotImplementedError, OSError):
            return False
        if (
            not stat.S_ISDIR(details.st_mode)
            or stat.S_ISLNK(details.st_mode)
            or (details.st_dev, details.st_ino) != step.identity
        ):
            return False
    return True


def _write_atomic_at(directory_fd: int, filename: str, payload: bytes, *, force: bool) -> None:
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise UnsafeExportTarget("default export filename escaped its directory")
    _validate_target_at(directory_fd, filename, force=force)
    temporary = f"{filename}.tmp"
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o666,
                dir_fd=directory_fd,
            )
        except FileExistsError as error:
            raise ExportError(f"temporary export path already exists: {temporary}") from error
        except (NotImplementedError, OSError) as error:
            raise ExportError("temporary export file could not be created safely") from error
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except (NotImplementedError, OSError) as error:
            raise ExportError("temporary export file could not be written safely") from error
        if force:
            _validate_target_at(directory_fd, filename, force=True)
            try:
                os.replace(
                    temporary,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                )
            except (NotImplementedError, OSError) as error:
                raise ExportError("export target could not be replaced atomically") from error
        else:
            try:
                os.link(
                    temporary,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError as error:
                raise ExportTargetExists(f"export target already exists: {filename}") from error
            except (NotImplementedError, OSError) as error:
                raise ExportError("export target could not be created atomically") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        except (NotImplementedError, OSError) as error:
            raise ExportError("temporary export file could not be cleaned safely") from error


def _write_bounded_default(
    anchor: Path,
    anchor_identity: tuple[int, int],
    steps: list[_DirectoryStep],
    filename: str,
    payload: bytes,
    *,
    force: bool,
) -> None:
    directory_fd = steps[-1].directory_fd
    _write_atomic_at(directory_fd, filename, payload, force=force)
    if not _directory_path_matches(anchor, anchor_identity) or not _directory_steps_match(steps):
        try:
            os.unlink(filename, dir_fd=directory_fd)
        except (NotImplementedError, OSError):
            pass
        raise UnsafeExportTarget("configured exports directory changed during publication")


def _write_atomic(target: Path, payload: bytes, *, force: bool) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise UnsafeExportTarget("export parent directory is unavailable") from error
    _validate_existing_target(target, force=force)
    temporary = target.with_name(f"{target.name}.tmp")
    try:
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as error:
            raise ExportError(f"temporary export path already exists: {temporary}") from error
        except OSError as error:
            raise ExportError("temporary export file could not be written safely") from error
        if force:
            _validate_existing_target(target, force=True)
            try:
                os.replace(temporary, target)
            except (NotImplementedError, OSError) as error:
                raise ExportError("export target could not be replaced atomically") from error
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise ExportTargetExists(f"export target already exists: {target}") from error
            except (NotImplementedError, OSError) as error:
                raise ExportError("export target could not be created atomically") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except (NotImplementedError, OSError) as error:
            raise ExportError("temporary export file could not be cleaned safely") from error


def export_video(request: VideoExportRequest, paths: DataPaths) -> ExportResult:
    """Export one transcript to VTT, normalized text or provenance Markdown."""
    output_format = request.format.strip().lower()
    if output_format not in {"vtt", "txt", "md"}:
        raise ExportError(f"unsupported export format: {request.format}")
    anchor_fd: int | None = None
    anchor_identity: tuple[int, int] | None = None
    anchor: Path | None = None
    exports: Path | None = None
    directory_steps: list[_DirectoryStep] = []
    if request.output is None:
        exports = _normalize_configured_exports(paths.exports)
        anchor = _filesystem_anchor(exports)
        exports_parts = _exports_relative_parts(anchor, exports)
        anchor_fd, anchor_identity = _open_validated_directory(anchor)
        try:
            directory_steps = _open_exports_from_root(anchor_fd, exports_parts)
        except ExportError:
            os.close(anchor_fd)
            anchor_fd = None
            raise

    try:
        resolved = resolve_transcript(paths.root, request.video_or_url, request.language)
        source_bytes = _read_source_snapshot(resolved.path)
        source_hash = sha256(source_bytes).hexdigest()
        snapshot = _VttSnapshot(source_bytes)

        if output_format == "vtt":
            payload = source_bytes
        elif output_format == "txt":
            payload = f"{clean_vtt(snapshot)}\n".encode("utf-8")  # type: ignore[arg-type]
        else:
            payload = _markdown(resolved, source_hash, snapshot).encode("utf-8")

        if request.output is None:
            assert exports is not None
            assert anchor is not None
            assert anchor_fd is not None
            assert anchor_identity is not None
            assert directory_steps
            filename = f"{resolved.video_id}.{resolved.language}.{output_format}"
            absolute_target = exports / filename
            _write_bounded_default(
                anchor,
                anchor_identity,
                directory_steps,
                filename,
                payload,
                force=request.force,
            )
        else:
            absolute_target = _absolute_without_following_symlinks(request.output)
            _write_atomic(absolute_target, payload, force=request.force)
    finally:
        for step in reversed(directory_steps):
            os.close(step.directory_fd)
        if anchor_fd is not None:
            os.close(anchor_fd)
    return ExportResult(
        path=absolute_target,
        source_sha256=source_hash,
        video_id=resolved.video_id,
        language=resolved.language,
        format=output_format,
    )
