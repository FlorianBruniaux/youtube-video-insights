"""YouTube subtitle downloader via yt-dlp subprocess."""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VideoInfo:
    video_id: str
    title: str
    upload_date: str  # YYYYMMDD or empty string
    channel_id: str = ""
    channel_title: str = ""

    @property
    def formatted_date(self) -> str:
        d = self.upload_date
        if len(d) == 8:
            return f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d or "unknown"

    @property
    def watch_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _source_args(source: str) -> list[str]:
    """Turn a SOURCE into yt-dlp positional args.

    A channel/playlist/video URL is passed as-is. A path to an existing local
    file (one URL per line) is passed via --batch-file, which is how yt-dlp
    reads a list of URLs. A bare positional file path is otherwise treated as a
    URL and rejected with "is not a valid URL".
    """
    if Path(source).is_file():
        return ["--batch-file", source]
    return [source]


@dataclass
class VideoListResult:
    videos: list[VideoInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    returncode: int = 0


def fetch_video_list(
    source: str, *, cookies_from_browser: str | None = None
) -> VideoListResult:
    """Fetch videos and retain yt-dlp failures for durable collection logs.

    Uses structured JSON from full metadata extraction so upload dates are exact.
    Media download remains disabled.
    """
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--skip-download",
        "--no-flat-playlist",
        "--ignore-errors",
    ]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd += _source_args(source)

    result = subprocess.run(cmd, capture_output=True, text=True)
    videos: list[VideoInfo] = []
    metadata_errors: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            if line.count("|") >= 2:
                upload_date, remainder = line.split("|", 1)
                title, vid_id = remainder.rsplit("|", 1)
                payload = {
                    "upload_date": upload_date,
                    "title": title,
                    "id": vid_id,
                }
            else:
                metadata_errors.append("yt-dlp emitted an invalid metadata record")
                continue
        if not isinstance(payload, dict):
            metadata_errors.append("yt-dlp emitted an invalid metadata record")
            continue
        raw_video_id = payload.get("id")
        raw_title = payload.get("title")
        raw_upload_date = payload.get("upload_date")
        if not isinstance(raw_video_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{11}", raw_video_id
        ):
            metadata_errors.append("yt-dlp emitted metadata without a valid video id")
            continue
        channel_id = _bounded_metadata_string(payload.get("channel_id"))
        channel_title = _bounded_metadata_string(
            payload.get("channel", payload.get("uploader"))
        )
        videos.append(
            VideoInfo(
                video_id=raw_video_id,
                title=raw_title if isinstance(raw_title, str) else raw_video_id,
                upload_date=(
                    raw_upload_date if isinstance(raw_upload_date, str) else ""
                ),
                channel_id=channel_id,
                channel_title=channel_title,
            )
        )

    errors = metadata_errors + [
        line.strip()
        for line in result.stderr.splitlines()
        if "ERROR" in line.upper()
    ]
    if result.returncode != 0 and not errors:
        detail = result.stderr.strip() or "no diagnostic output"
        errors.append(f"yt-dlp exited with status {result.returncode}: {detail}")

    return VideoListResult(
        videos=videos,
        errors=errors,
        returncode=result.returncode,
    )


def _bounded_metadata_string(value: object) -> str:
    if not isinstance(value, str) or len(value) > 300 or "\x00" in value:
        return ""
    return value


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)


@contextmanager
def _confined_directory(
    root: Path, destination: Path, *, create: bool
) -> Iterator[int]:
    """Open destination from `/` with no-follow directory descriptors held open."""
    absolute_root = root.expanduser().absolute()
    absolute_destination = destination.expanduser().absolute()
    try:
        absolute_destination.relative_to(absolute_root)
    except ValueError as exc:
        raise ValueError("destination must remain under data root") from exc
    if not getattr(os, "O_NOFOLLOW", 0) or not getattr(os, "O_DIRECTORY", 0):
        raise ValueError("platform lacks safe no-follow directory operations")

    descriptors: list[int] = [os.open("/", _DIRECTORY_FLAGS)]
    try:
        current = descriptors[-1]
        for component in absolute_destination.parts[1:]:
            if component in {"", ".", ".."}:
                raise ValueError("unsafe destination component")
            try:
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode=0o755, dir_fd=current)
                child = os.open(component, _DIRECTORY_FLAGS, dir_fd=current)
            except OSError as exc:
                raise ValueError(
                    f"unsafe, unstable, or symlink directory component: {component}"
                ) from exc
            descriptors.append(child)
            current = child
        yield descriptors[-1]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _list_regular_names(
    directory_fd: int, *, suffix: str | None = None, reject_unsafe: bool = True
) -> tuple[str, ...]:
    names: list[str] = []
    for name in os.listdir(directory_fd):
        try:
            details = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError:
            if reject_unsafe:
                raise ValueError(  # noqa: B904 - preserve filesystem error context
                    f"directory entry changed during inventory: {name}"
                )
            continue
        if not stat.S_ISREG(details.st_mode):
            if reject_unsafe:
                raise ValueError(f"directory contains a non-regular entry: {name}")
            continue
        if suffix is None or name.endswith(suffix):
            names.append(name)
    return tuple(sorted(names))


def _read_regular_at(
    directory_fd: int, name: str, *, max_bytes: int | None = None
) -> bytes:
    if Path(name).name != name or "\x00" in name:
        raise ValueError("unsafe file name")
    descriptor = os.open(name, _READ_FLAGS | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"not a regular file: {name}")
        if max_bytes is not None and opened.st_size > max_bytes:
            raise ValueError(f"file exceeds byte limit: {name}")
        remaining = opened.st_size + 1 if max_bytes is None else max_bytes + 1
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        final = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        if (
            (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != identity
            or len(payload) != opened.st_size
            or (max_bytes is not None and len(payload) > max_bytes)
        ):
            raise ValueError(f"file changed while being read: {name}")
        return payload
    finally:
        os.close(descriptor)


def _copy_regular_at(directory_fd: int, name: str, destination: Path) -> None:
    """Copy one stable no-follow directory entry into a private path."""
    if Path(name).name != name or "\x00" in name:
        raise ValueError("unsafe copied file name")
    source_fd = os.open(name, _READ_FLAGS | os.O_NONBLOCK, dir_fd=directory_fd)
    destination_fd: int | None = None
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"not a regular file: {name}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        copied = 0
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
            copied += len(chunk)
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or copied != before.st_size
        ):
            raise ValueError(f"file changed while being copied: {name}")
    except Exception:
        with suppress(OSError):
            destination.unlink(missing_ok=True)
        raise
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(source_fd)


def _copy_path_to_at(source: Path, destination_fd: int, name: str) -> tuple[int, int]:
    """Copy a stable private source to a new entry through a held dirfd."""
    if Path(name).name != name or "\x00" in name:
        raise ValueError("unsafe published file name")
    source_fd = os.open(source, _READ_FLAGS | os.O_NONBLOCK)
    output_fd: int | None = None
    created = False
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"published source is not regular: {name}")
        output_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination_fd,
        )
        created = True
        copied = 0
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(output_fd, view)
                view = view[written:]
            copied += len(chunk)
        os.fsync(output_fd)
        after = os.fstat(source_fd)
        if (
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            or copied != before.st_size
        ):
            raise ValueError(f"published source changed while copying: {name}")
        published = os.fstat(output_fd)
        return published.st_dev, published.st_ino
    except Exception:
        if created and output_fd is not None:
            try:
                opened = os.fstat(output_fd)
                current = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino):
                    os.unlink(name, dir_fd=destination_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        if output_fd is not None:
            os.close(output_fd)
        os.close(source_fd)


def _publish_new_regular_file(source: Path, destination_fd: int, name: str) -> None:
    """Publish a new file and refuse every pre-existing destination."""
    try:
        _copy_path_to_at(source, destination_fd, name)
        os.fsync(destination_fd)
    except FileExistsError as exc:
        raise ValueError(f"publication target already exists: {name}") from exc


def _replace_regular_file(
    source: Path,
    destination_fd: int,
    name: str,
    *,
    before_replace: Callable[[], None] | None = None,
) -> None:
    """Copy privately, then atomically replace a destination through its dirfd."""
    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    temporary_identity: tuple[int, int] | None = None
    try:
        temporary_identity = _copy_path_to_at(source, destination_fd, temporary_name)
        current = os.stat(
            temporary_name, dir_fd=destination_fd, follow_symlinks=False
        )
        if (current.st_dev, current.st_ino) != temporary_identity:
            raise ValueError("database publication temporary changed")
        if before_replace is not None:
            before_replace()
        os.replace(
            temporary_name,
            name,
            src_dir_fd=destination_fd,
            dst_dir_fd=destination_fd,
        )
        os.fsync(destination_fd)
    finally:
        try:
            current = os.stat(
                temporary_name, dir_fd=destination_fd, follow_symlinks=False
            )
            if temporary_identity is not None and (
                current.st_dev,
                current.st_ino,
            ) == temporary_identity:
                os.unlink(temporary_name, dir_fd=destination_fd)
        except FileNotFoundError:
            pass


def _promote_regular_file(source: Path, destination_fd: int, name: str) -> bool:
    """Copy a stable source to a private sibling, then publish without overwrite."""
    if Path(name).name != name or "\x00" in name:
        raise ValueError("unsafe promoted file name")
    try:
        existing = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode):
            raise ValueError(f"destination is not a regular file: {name}")
        return False

    source_fd = os.open(source, _READ_FLAGS | os.O_NONBLOCK)
    temporary_name = f".{name}.{secrets.token_hex(8)}.tmp"
    temporary_fd: int | None = None
    try:
        source_before = os.fstat(source_fd)
        if not stat.S_ISREG(source_before.st_mode):
            raise ValueError(f"staged source is not regular: {name}")
        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination_fd,
        )
        copied = 0
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(temporary_fd, view)
                view = view[written:]
            copied += len(chunk)
        os.fsync(temporary_fd)
        source_after = os.fstat(source_fd)
        if (
            (source_after.st_dev, source_after.st_ino, source_after.st_size, source_after.st_mtime_ns)
            != (
                source_before.st_dev,
                source_before.st_ino,
                source_before.st_size,
                source_before.st_mtime_ns,
            )
            or copied != source_before.st_size
        ):
            raise ValueError(f"staged source changed while copying: {name}")
        os.close(temporary_fd)
        temporary_fd = None
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=destination_fd,
                dst_dir_fd=destination_fd,
                follow_symlinks=False,
            )
            return True
        except FileExistsError:
            existing = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
            if not stat.S_ISREG(existing.st_mode):
                raise ValueError(  # noqa: B904 - preserve filesystem error context
                    f"destination changed to a non-regular file: {name}"
                )
            return False
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        os.close(source_fd)
        with suppress(FileNotFoundError):
            os.unlink(temporary_name, dir_fd=destination_fd)


def list_videos(source: str, *, cookies_from_browser: str | None = None) -> list[VideoInfo]:
    """Compatibility wrapper returning only the discovered video list."""
    return fetch_video_list(
        source,
        cookies_from_browser=cookies_from_browser,
    ).videos


def vtt_to_video_info(vtt_path: Path) -> VideoInfo:
    """Parse a VTT filename into VideoInfo.

    Expected format: YYYYMMDD - Title [videoID].fr.vtt
    """
    stem = vtt_path.stem  # e.g. "20260223 - Title [nfupYzLjFGc].fr"
    stem = re.sub(r"\.(fr|en)$", "", stem)
    m_id = re.search(r"\[([A-Za-z0-9_-]+)\]$", stem)
    vid_id = m_id.group(1) if m_id else ""
    m_date = re.match(r"^(\d{8})\s*-\s*", stem)
    date = m_date.group(1) if m_date else ""
    title = re.sub(r"^\d{8}\s*-\s*", "", stem)
    title = re.sub(r"\s*\[[A-Za-z0-9_-]+\]$", "", title).strip()
    return VideoInfo(video_id=vid_id, title=title, upload_date=date)


@dataclass
class DownloadResult:
    vtt_files: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_count: int = 0
    returncode: int = 0


def download_subtitles(
    channel_url: str,
    output_dir: Path,
    *,
    sleep_requests: int = 0,
    cookies_from_browser: str | None = None,
    sub_langs: str = "fr,en",
    data_root: Path | None = None,
) -> DownloadResult:
    """Download auto-generated subtitles from a YouTube channel/playlist/video.

    Detects new VTT files via a before/after snapshot of output_dir.
    (--print after_move:filepath only fires for media downloads, not subtitles.)
    """
    requested_root = (data_root or output_dir.parent).expanduser().absolute()
    requested_output = output_dir.expanduser().absolute()
    relative_output = requested_output.relative_to(requested_root)
    root = Path(os.path.realpath(requested_root))
    stable_output = root / relative_output
    with _confined_directory(root, stable_output, create=True) as destination_fd:
        destination_names = _list_regular_names(destination_fd, reject_unsafe=True)
        with tempfile.TemporaryDirectory(prefix="yt-insights-download-") as staging_name:
            staging = Path(staging_name)
            cached_vtt_names: set[str] = set()
            for name in destination_names:
                if not (name.endswith(".vtt") or name.endswith(".info.json")):
                    continue
                _copy_regular_at(destination_fd, name, staging / name)
                if name.endswith(".vtt"):
                    cached_vtt_names.add(name)
            cmd = [
                "yt-dlp",
                "--write-auto-subs",
                "--sub-langs", sub_langs,
                "--sub-format", "vtt",
                "--skip-download",
                "--write-info-json",
                "--no-write-playlist-metafiles",
                "--ignore-errors",
                "--output", str(staging / "%(upload_date)s - %(title)s [%(id)s].%(ext)s"),
                "--extractor-retries", "5",
                "--retry-sleep", "extractor:exp=1:30",
            ]
            if sleep_requests > 0:
                cmd += ["--sleep-requests", str(sleep_requests)]
            if cookies_from_browser:
                cmd += ["--cookies-from-browser", cookies_from_browser]
            cmd += _source_args(channel_url)
            result = subprocess.run(cmd, capture_output=True, text=True)

            errors = [
                line.strip()
                for line in (result.stdout + "\n" + result.stderr).splitlines()
                if "ERROR" in line.upper()
            ]
            if result.returncode != 0 and not errors:
                detail = result.stderr.strip() or "no diagnostic output"
                errors.append(f"yt-dlp exited with status {result.returncode}: {detail}")

            selected_vtt_names: set[str] = set()
            skipped_vtt_names: set[str] = set()
            for entry in staging.iterdir():
                details = entry.lstat()
                if not stat.S_ISREG(details.st_mode):
                    raise ValueError(f"yt-dlp staged a non-regular entry: {entry.name}")
                promoted = _promote_regular_file(entry, destination_fd, entry.name)
                if entry.name.endswith(".vtt"):
                    selected_vtt_names.add(entry.name)
                    if not promoted:
                        skipped_vtt_names.add(entry.name)

            for line in (result.stdout + "\n" + result.stderr).splitlines():
                marker = "Subtitle file already exists:"
                if marker in line:
                    name = Path(line.split(marker, 1)[1].strip()).name
                    if name.endswith(".vtt"):
                        selected_vtt_names.add(name)
                        skipped_vtt_names.add(name)
                marker = "Writing video subtitles to:"
                if marker in line:
                    name = Path(line.split(marker, 1)[1].strip()).name
                    if name.endswith(".vtt"):
                        selected_vtt_names.add(name)

            available = set(_list_regular_names(destination_fd, suffix=".vtt"))
            return DownloadResult(
                vtt_files=[output_dir / name for name in sorted(selected_vtt_names & available)],
                errors=errors,
                skipped_count=len(skipped_vtt_names | cached_vtt_names),
                returncode=result.returncode,
            )
