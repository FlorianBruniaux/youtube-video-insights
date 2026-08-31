"""Safe installer for the optional Claude Code and Codex integration."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NotRequired, TypedDict

Client = Literal["claude", "codex"]
Mode = Literal["dry-run", "apply", "verify"]
MCP_NAME = "yt-insights"
OWNERSHIP_MANIFEST_RELATIVE = Path(".agents/.yt-insights-assistant-assets-v1.json")
OWNERSHIP_SCHEMA_VERSION = 1
RECOVERY_DIRECTORY_NAME = ".yt-insights-assistant-recovery-v1"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_READ_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW


@dataclass(frozen=True)
class Asset:
    source: Path
    target: Path


@dataclass(frozen=True)
class FileSnapshot:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class CreatedFile:
    path: Path
    parent_fd: int
    name: str
    installed: FileSnapshot


@dataclass(frozen=True)
class AssetState:
    status: str
    source_sha256: str
    live: FileSnapshot | None


@dataclass(frozen=True)
class OwnershipManifest:
    path: Path
    status: str
    assets: dict[str, str]
    snapshot: FileSnapshot | None


@dataclass(frozen=True)
class ReplacedFile:
    path: Path
    parent_fd: int
    name: str
    installed: FileSnapshot
    backup_name: str
    backup: FileSnapshot


@dataclass(frozen=True)
class CreatedDirectory:
    parent_fd: int
    name: str
    device: int
    inode: int


@dataclass
class BoundRoot:
    home: Path
    fd: int
    parent_fd: int
    name: str
    device: int
    inode: int
    created_home: bool
    created_directories: list[CreatedDirectory]
    recovery_paths: list[str]


@dataclass
class AssetTransaction:
    created: list[CreatedFile]
    replaced: list[ReplacedFile]
    root: BoundRoot


@dataclass(frozen=True)
class QuarantinedFile:
    name: str
    original_name: str
    snapshot: FileSnapshot


class PublicationConflict(ValueError):
    def __init__(self, message: str, *, recovery_paths: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.recovery_paths = recovery_paths


class AssistantSetupOperation(TypedDict):
    kind: str
    status: str
    target: NotRequired[str]
    client: NotRequired[Client]
    name: NotRequired[str]


class AssistantSetupCleanupFailure(TypedDict):
    client: Client
    error: str


class AssistantSetupPayload(TypedDict):
    mode: Mode
    clients: list[Client]
    assets_only: bool
    operations: list[AssistantSetupOperation]
    status: NotRequired[str]
    data_root: NotRequired[str]
    mcp_command: NotRequired[str]
    conflicts: NotRequired[list[str]]
    existing_clients: NotRequired[list[Client]]
    invalid_assets: NotRequired[list[str]]
    invalid_manifest: NotRequired[str | None]
    missing_clients: NotRequired[list[Client]]
    error: NotRequired[str]
    recovery_paths: NotRequired[list[str]]
    failed_client_cleanup: NotRequired[list[AssistantSetupCleanupFailure]]
    created_files: NotRequired[int]
    upgraded_files: NotRequired[int]
    registered_clients: NotRequired[list[Client]]
    cleanup_pending: NotRequired[list[str]]


def _asset_root() -> Path:
    return Path(__file__).resolve().parent / "assistant_assets"


def _assets(home: Path, clients: tuple[Client, ...]) -> tuple[Asset, ...]:
    root = _asset_root()
    assets: list[Asset] = []
    for skill in (
        "youtube-acquire",
        "youtube-research",
        "youtube-export",
        "youtube-cumulative-research",
    ):
        for relative in (Path("SKILL.md"), Path("agents/openai.yaml")):
            assets.append(
                Asset(
                    root / "skills" / skill / relative,
                    home / ".agents" / "skills" / skill / relative,
                )
            )
    if "claude" in clients:
        assets.append(
            Asset(
                root / "claude" / "youtube-corpus-researcher.md",
                home / ".claude" / "agents" / "youtube-corpus-researcher.md",
            )
        )
    if "codex" in clients:
        assets.append(
            Asset(
                root / "codex" / "youtube-corpus-researcher.toml",
                home / ".codex" / "agents" / "youtube-corpus-researcher.toml",
            )
        )
    return tuple(assets)


def resolve_clients(value: str) -> tuple[Client, ...]:
    if value == "both":
        return ("claude", "codex")
    if value in {"claude", "codex"}:
        return (value,)  # type: ignore[return-value]
    raise ValueError(f"Unsupported client: {value}")


def _resolve_executable(value: str, *, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate
    else:
        found = shutil.which(value)
        if found is None:
            raise ValueError(f"{label} executable was not found: {value}")
        resolved = Path(found)
    if not resolved.is_absolute() or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"{label} must resolve to an absolute executable file: {value}")
    return resolved.resolve()


def _client_executables(clients: tuple[Client, ...]) -> dict[Client, Path]:
    return {client: _resolve_executable(client, label=client) for client in clients}


def _has_unsafe_parent(target: Path, home: Path) -> bool:
    parent = target.parent
    while parent != home:
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            return True
        if home not in parent.parents:
            return True
        parent = parent.parent
    return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot_file(path: Path) -> tuple[FileSnapshot, bytes]:
    before = path.lstat()
    if path.is_symlink() or not path.is_file():
        raise ValueError("managed file is not a regular file")
    data = path.read_bytes()
    after = path.lstat()
    identity_before = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    )
    identity_after = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )
    if identity_before != identity_after:
        raise ValueError("managed file changed during inspection")
    return (
        FileSnapshot(
            device=after.st_dev,
            inode=after.st_ino,
            size=after.st_size,
            mtime_ns=after.st_mtime_ns,
            sha256=_sha256(data),
        ),
        data,
    )


def _same_snapshot(left: FileSnapshot, right: FileSnapshot) -> bool:
    return left == right


def _target_relative(target: Path, home: Path) -> str:
    return target.relative_to(home).as_posix()


def _allowed_manifest_targets(home: Path) -> frozenset[str]:
    return frozenset(
        _target_relative(asset.target, home)
        for asset in _assets(home, ("claude", "codex"))
    )


def _ownership_manifest(home: Path) -> OwnershipManifest:
    path = home / OWNERSHIP_MANIFEST_RELATIVE
    if _has_unsafe_parent(path, home) or path.is_symlink():
        return OwnershipManifest(path, "conflict", {}, None)
    if not path.exists():
        return OwnershipManifest(path, "missing", {}, None)
    try:
        snapshot, raw = _snapshot_file(path)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "assets"}:
            raise ValueError("ownership manifest shape is invalid")
        if payload["schema_version"] != OWNERSHIP_SCHEMA_VERSION:
            raise ValueError("ownership manifest version is invalid")
        assets = payload["assets"]
        allowed = _allowed_manifest_targets(home)
        if not isinstance(assets, dict) or any(
            not isinstance(relative, str)
            or relative not in allowed
            or not isinstance(digest, str)
            or _SHA256_PATTERN.fullmatch(digest) is None
            for relative, digest in assets.items()
        ):
            raise ValueError("ownership manifest assets are invalid")
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return OwnershipManifest(path, "conflict", {}, None)
    return OwnershipManifest(path, "valid", dict(assets), snapshot)


def _asset_state(
    asset: Asset,
    home: Path,
    ownership: OwnershipManifest,
) -> AssetState:
    source_digest = _sha256(asset.source.read_bytes())
    if _has_unsafe_parent(asset.target, home):
        return AssetState("conflict", source_digest, None)
    if asset.target.is_symlink():
        return AssetState("conflict", source_digest, None)
    if not asset.target.exists():
        return AssetState("missing", source_digest, None)
    if not asset.target.is_file():
        return AssetState("conflict", source_digest, None)
    try:
        snapshot, _ = _snapshot_file(asset.target)
    except (OSError, ValueError):
        return AssetState("conflict", source_digest, None)
    if snapshot.sha256 == source_digest:
        return AssetState("identical", source_digest, snapshot)
    relative = _target_relative(asset.target, home)
    if (
        ownership.status == "valid"
        and ownership.assets.get(relative) == snapshot.sha256
    ):
        return AssetState("upgrade", source_digest, snapshot)
    return AssetState("conflict", source_digest, snapshot)


def _get_registration(executable: Path) -> bool:
    result = subprocess.run(
        [str(executable), "mcp", "get", MCP_NAME],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _registration_command(
    client: Client,
    executable: Path,
    server: Path,
    data_root: Path,
) -> list[str]:
    environment = (
        f"YT_INSIGHTS_DATA_ROOT={data_root}",
        f"YT_INSIGHTS_SEARCH_DATABASE={data_root / '.search' / 'search-v1.sqlite3'}",
        f"YT_INSIGHTS_CATALOG_DATABASE={data_root / 'catalog.sqlite3'}",
    )
    command = [str(executable), "mcp", "add"]
    if client == "claude":
        command.extend(["--scope", "user"])
    for value in environment:
        command.extend(["--env", value])
    command.append(MCP_NAME)
    if client == "codex":
        command.append("--")
    command.append(str(server))
    return command


def _remove_registration(client: Client, executable: Path) -> None:
    command = [str(executable), "mcp", "remove", MCP_NAME]
    if client == "claude":
        command.extend(["--scope", "user"])
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"{client} MCP cleanup failed with exit code {result.returncode}"
        )


def _snapshot_at(parent_fd: int, name: str) -> tuple[FileSnapshot, bytes]:
    descriptor = os.open(name, _READ_FILE_FLAGS, dir_fd=parent_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("managed file is not a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        identity_before = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        identity_after = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        identity_entry = (
            entry.st_dev,
            entry.st_ino,
            entry.st_size,
            entry.st_mtime_ns,
        )
        if identity_before != identity_after or identity_after != identity_entry:
            raise ValueError("managed file changed during inspection")
        return (
            FileSnapshot(
                device=after.st_dev,
                inode=after.st_ino,
                size=after.st_size,
                mtime_ns=after.st_mtime_ns,
                sha256=_sha256(raw),
            ),
            raw,
        )
    finally:
        os.close(descriptor)


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        raise ValueError("directory path must be absolute")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_bound_root(home: Path) -> BoundRoot:
    parent_fd = _open_absolute_directory(home.parent)
    descriptor: int | None = None
    created = False
    created_identity: tuple[int, int] | None = None
    recovery_paths: list[str] = []
    try:
        try:
            descriptor = os.open(home.name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            temporary = _unique_entry_name(
                parent_fd,
                f"{home.name}.directory",
            )
            os.mkdir(temporary, 0o755, dir_fd=parent_fd)
            descriptor = os.open(temporary, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            owned = os.fstat(descriptor)
            try:
                _rename_no_replace_at(parent_fd, temporary, home.name)
            except FileExistsError:
                retained = _retain_owned_temporary_directory(
                    parent_fd,
                    temporary,
                    device=owned.st_dev,
                    inode=owned.st_ino,
                )
                recovery_paths.append(str(home.parent / retained))
                os.close(descriptor)
                descriptor = None
                descriptor = os.open(
                    home.name,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_fd,
                )
            except Exception as error:
                retained = _retain_owned_temporary_directory(
                    parent_fd,
                    temporary,
                    device=owned.st_dev,
                    inode=owned.st_ino,
                )
                os.close(descriptor)
                descriptor = None
                raise PublicationConflict(
                    str(error),
                    recovery_paths=(str(home.parent / retained),),
                ) from error
            else:
                if not _directory_entry_matches(
                    parent_fd,
                    home.name,
                    device=owned.st_dev,
                    inode=owned.st_ino,
                ):
                    os.close(descriptor)
                    descriptor = None
                    raise PublicationConflict(
                        "published assistant home does not match owned inode"
                    )
                created = True
                created_identity = (owned.st_dev, owned.st_ino)
        if descriptor is None:
            raise PublicationConflict("assistant home descriptor was not established")
        current = os.fstat(descriptor)
        return BoundRoot(
            home=home,
            fd=descriptor,
            parent_fd=parent_fd,
            name=home.name,
            device=current.st_dev,
            inode=current.st_ino,
            created_home=created,
            created_directories=[],
            recovery_paths=recovery_paths,
        )
    except Exception as error:
        if created and created_identity is not None:
            recovery_paths.append(str(home))
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        os.close(parent_fd)
        if recovery_paths:
            raise PublicationConflict(
                str(error),
                recovery_paths=tuple(sorted(set(recovery_paths))),
            ) from error
        raise


def _assert_root_current(root: BoundRoot) -> None:
    current = os.stat(root.name, dir_fd=root.parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        root.device,
        root.inode,
    ):
        raise ValueError("assistant home changed during publication")


def _rename_no_replace_between(
    source_fd: int,
    source_name: str,
    target_fd: int,
    target_name: str,
) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source = os.fsencode(source_name)
    target = os.fsencode(target_name)
    if hasattr(library, "renameatx_np"):
        rename = library.renameatx_np
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(source_fd, source, target_fd, target, 0x00000004)
    elif hasattr(library, "renameat2"):
        rename = library.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(source_fd, source, target_fd, target, 0x00000001)
    else:
        raise OSError(
            errno.ENOTSUP,
            "exclusive directory publication is unavailable on this platform",
        )
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(error, os.strerror(error), target_name)
        raise OSError(error, os.strerror(error), target_name)


def _rename_no_replace_at(parent_fd: int, source_name: str, target_name: str) -> None:
    _rename_no_replace_between(
        parent_fd,
        source_name,
        parent_fd,
        target_name,
    )


def _directory_entry_matches(
    parent_fd: int,
    name: str,
    *,
    device: int,
    inode: int,
) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == (
        device,
        inode,
    )


def _retain_owned_temporary_directory(
    parent_fd: int,
    name: str,
    *,
    device: int,
    inode: int,
) -> str:
    if not _directory_entry_matches(
        parent_fd,
        name,
        device=device,
        inode=inode,
    ):
        raise PublicationConflict(
            "owned temporary directory changed before recovery",
            recovery_paths=(name,),
        )
    recovery = _unique_entry_name(parent_fd, f"{name}.directory-recovery")
    _rename_no_replace_at(parent_fd, name, recovery)
    if not _directory_entry_matches(
        parent_fd,
        recovery,
        device=device,
        inode=inode,
    ):
        raise PublicationConflict(
            "temporary directory changed during recovery",
            recovery_paths=(recovery,),
        )
    return recovery


def _record_created_directory(
    root: BoundRoot,
    parent_fd: int,
    name: str,
    child_fd: int,
) -> None:
    child = os.fstat(child_fd)
    root.created_directories.append(
        CreatedDirectory(
            parent_fd=os.dup(parent_fd),
            name=name,
            device=child.st_dev,
            inode=child.st_ino,
        )
    )


def _open_parent_at(root: BoundRoot, target: Path, *, create: bool) -> int:
    relative_parent = target.parent.relative_to(root.home)
    descriptor = os.dup(root.fd)
    current_parent = root.home
    try:
        for part in relative_parent.parts:
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                temporary = _unique_entry_name(
                    descriptor,
                    f"{part}.directory",
                )
                os.mkdir(temporary, 0o755, dir_fd=descriptor)
                child = os.open(temporary, _DIRECTORY_FLAGS, dir_fd=descriptor)
                owned = os.fstat(child)
                try:
                    _rename_no_replace_at(descriptor, temporary, part)
                except FileExistsError:
                    retained = _retain_owned_temporary_directory(
                        descriptor,
                        temporary,
                        device=owned.st_dev,
                        inode=owned.st_ino,
                    )
                    root.recovery_paths.append(str(current_parent / retained))
                    os.close(child)
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except Exception as error:
                    retained = _retain_owned_temporary_directory(
                        descriptor,
                        temporary,
                        device=owned.st_dev,
                        inode=owned.st_ino,
                    )
                    os.close(child)
                    raise PublicationConflict(
                        str(error),
                        recovery_paths=(
                            str(current_parent / retained),
                        ),
                    ) from error
                else:
                    if not _directory_entry_matches(
                        descriptor,
                        part,
                        device=owned.st_dev,
                        inode=owned.st_ino,
                    ):
                        os.close(child)
                        raise PublicationConflict(
                            "published directory does not match owned inode"
                        )
                    _record_created_directory(root, descriptor, part, child)
            os.close(descriptor)
            descriptor = child
            current_parent /= part
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _assert_parent_current(parent_fd: int, parent: Path) -> None:
    bound = os.fstat(parent_fd)
    current = os.stat(parent, follow_symlinks=False)
    if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (
        bound.st_dev,
        bound.st_ino,
    ):
        raise ValueError("managed asset parent changed during publication")


def _unique_entry_name(parent_fd: int, prefix: str) -> str:
    for _ in range(128):
        name = f".{prefix}.{secrets.token_hex(12)}"
        try:
            os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return name
    raise RuntimeError("could not reserve a temporary entry name")


def _stage_bytes_at(
    parent_fd: int,
    parent: Path,
    target_name: str,
    content: bytes,
) -> tuple[str, FileSnapshot]:
    stage_name = _unique_entry_name(parent_fd, f"{target_name}.new")
    descriptor = os.open(
        stage_name,
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    staged: FileSnapshot | None = None
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.fchmod(descriptor, 0o644)
        current = os.fstat(descriptor)
        staged = FileSnapshot(
            device=current.st_dev,
            inode=current.st_ino,
            size=current.st_size,
            mtime_ns=current.st_mtime_ns,
            sha256=_sha256(content),
        )
    except Exception as error:
        current = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        partial = FileSnapshot(
            device=current.st_dev,
            inode=current.st_ino,
            size=current.st_size,
            mtime_ns=current.st_mtime_ns,
            sha256=_sha256(b"".join(chunks)),
        )
        os.close(descriptor)
        try:
            cleanup_paths = _remove_expected_entry(
                parent_fd,
                parent,
                stage_name,
                partial,
                purpose="stage-write-failure",
            )
        except PublicationConflict as cleanup_error:
            cleanup_paths = list(cleanup_error.recovery_paths)
        except OSError:
            cleanup_paths = [str(parent / stage_name)]
        raise PublicationConflict(
            str(error),
            recovery_paths=tuple(cleanup_paths),
        ) from error
    finally:
        if staged is not None:
            os.close(descriptor)
    try:
        verified, _ = _snapshot_at(parent_fd, stage_name)
        if not _same_snapshot(verified, staged):
            raise ValueError("staged file changed before publication")
        return stage_name, staged
    except Exception as error:
        verification_recovery_paths: list[str] = []
        if staged is not None:
            try:
                verification_recovery_paths.extend(
                    _remove_expected_entry(
                        parent_fd,
                        parent,
                        stage_name,
                        staged,
                        purpose="stage-verification-failure",
                    )
                )
            except PublicationConflict as cleanup_error:
                verification_recovery_paths.extend(cleanup_error.recovery_paths)
            except OSError:
                verification_recovery_paths.append(str(parent / stage_name))
        raise PublicationConflict(
            str(error),
            recovery_paths=tuple(verification_recovery_paths),
        ) from error


def _entry_matches_snapshot(parent_fd: int, name: str, expected: FileSnapshot) -> bool:
    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(current.st_mode) and (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) == (
        expected.device,
        expected.inode,
        expected.size,
        expected.mtime_ns,
    )


def _quarantine_expected_entry(
    parent_fd: int,
    parent: Path,
    name: str,
    expected: FileSnapshot,
    *,
    purpose: str,
) -> QuarantinedFile:
    quarantine = _unique_entry_name(
        parent_fd,
        f"{name}.{purpose}.quarantine",
    )
    try:
        _rename_no_replace_at(parent_fd, name, quarantine)
    except FileNotFoundError as error:
        raise PublicationConflict(
            "expected transaction entry is missing during finalization",
            recovery_paths=(str(parent / name),),
        ) from error
    try:
        quarantined, _ = _snapshot_at(parent_fd, quarantine)
    except Exception as error:
        raise PublicationConflict(
            "quarantined entry could not be verified",
            recovery_paths=(str(parent / quarantine),),
        ) from error
    if _same_snapshot(quarantined, expected):
        return QuarantinedFile(quarantine, name, quarantined)

    recovery = _unique_entry_name(parent_fd, f"{name}.conflict-recovery")
    try:
        _rename_no_replace_at(parent_fd, quarantine, recovery)
    except Exception as error:
        raise PublicationConflict(
            "foreign quarantined entry could not be moved to recovery",
            recovery_paths=(str(parent / quarantine),),
        ) from error
    with suppress(FileExistsError):
        os.link(
            recovery,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
    raise PublicationConflict(
        "foreign entry replaced an owned publication target",
        recovery_paths=(str(parent / recovery),),
    )


def _open_recovery_directory(parent_fd: int, parent: Path) -> tuple[int, list[str]]:
    try:
        return (
            os.open(
                RECOVERY_DIRECTORY_NAME,
                _DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            ),
            [],
        )
    except FileNotFoundError:
        temporary = _unique_entry_name(
            parent_fd,
            f"{RECOVERY_DIRECTORY_NAME}.directory",
        )
        os.mkdir(temporary, 0o700, dir_fd=parent_fd)
        descriptor = os.open(temporary, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        owned = os.fstat(descriptor)
        try:
            _rename_no_replace_at(
                parent_fd,
                temporary,
                RECOVERY_DIRECTORY_NAME,
            )
        except FileExistsError:
            retained = _unique_entry_name(
                parent_fd,
                f"{RECOVERY_DIRECTORY_NAME}.directory-recovery",
            )
            _rename_no_replace_at(parent_fd, temporary, retained)
            os.close(descriptor)
            return (
                os.open(
                    RECOVERY_DIRECTORY_NAME,
                    _DIRECTORY_FLAGS,
                    dir_fd=parent_fd,
                ),
                [str(parent / retained)],
            )
        if not _directory_entry_matches(
            parent_fd,
            RECOVERY_DIRECTORY_NAME,
            device=owned.st_dev,
            inode=owned.st_ino,
        ):
            os.close(descriptor)
            raise PublicationConflict(
                "private recovery directory changed during publication",
                recovery_paths=(str(parent / RECOVERY_DIRECTORY_NAME),),
            ) from None
        return descriptor, []


def _recovery_entry_name(
    recovery_fd: int,
    *,
    name: str,
    purpose: str,
    snapshot: FileSnapshot,
) -> str:
    identity = _sha256(
        f"{name}\0{purpose}\0{snapshot.sha256}".encode()
    )[:16]
    for sequence in range(1, 10_000):
        candidate = f"{identity}-{sequence:04d}-{purpose}-{name}"
        if not _entry_exists(recovery_fd, candidate):
            return candidate
    raise RuntimeError("private recovery directory is exhausted")


def _retain_quarantined_entry(
    parent_fd: int,
    parent: Path,
    quarantined: QuarantinedFile,
    *,
    purpose: str,
) -> list[str]:
    recovery_fd, retained = _open_recovery_directory(parent_fd, parent)
    try:
        recovery_name = _recovery_entry_name(
            recovery_fd,
            name=quarantined.original_name,
            purpose=purpose,
            snapshot=quarantined.snapshot,
        )
        _rename_no_replace_between(
            parent_fd,
            quarantined.name,
            recovery_fd,
            recovery_name,
        )
        recovered, _ = _snapshot_at(recovery_fd, recovery_name)
        recovery_path = parent / RECOVERY_DIRECTORY_NAME / recovery_name
        retained.append(str(recovery_path))
        if not _same_snapshot(recovered, quarantined.snapshot):
            with suppress(FileExistsError):
                os.link(
                    recovery_name,
                    quarantined.original_name,
                    src_dir_fd=recovery_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            raise PublicationConflict(
                "foreign content reached the private recovery directory",
                recovery_paths=tuple(retained),
            )
        os.fsync(recovery_fd)
        return retained
    finally:
        os.close(recovery_fd)


def _remove_expected_entry(
    parent_fd: int,
    parent: Path,
    name: str,
    expected: FileSnapshot,
    *,
    purpose: str,
) -> list[str]:
    quarantined = _quarantine_expected_entry(
        parent_fd,
        parent,
        name,
        expected,
        purpose=purpose,
    )
    return _retain_quarantined_entry(
        parent_fd,
        parent,
        quarantined,
        purpose=purpose,
    )


def _entry_exists(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _publish_staged_at(
    parent_fd: int,
    parent: Path,
    stage_name: str,
    staged: FileSnapshot,
    target_name: str,
) -> FileSnapshot:
    published = False
    try:
        _rename_no_replace_at(parent_fd, stage_name, target_name)
        published = True
        installed, _ = _snapshot_at(parent_fd, target_name)
        if not _same_snapshot(installed, staged):
            raise ValueError("published file does not match staged inode")
        _assert_parent_current(parent_fd, parent)
        return installed
    except Exception as error:
        failed_name = target_name if published else stage_name
        recovery_paths: list[str]
        try:
            recovery_paths = _remove_expected_entry(
                parent_fd,
                parent,
                failed_name,
                staged,
                purpose="failed-publication",
            )
        except PublicationConflict as cleanup_error:
            recovery_paths = list(cleanup_error.recovery_paths)
        except OSError:
            recovery_paths = [str(parent / failed_name)]
        raise PublicationConflict(
            str(error),
            recovery_paths=tuple(recovery_paths),
        ) from error


def _write_asset(asset: Asset, root: BoundRoot) -> CreatedFile:
    return _write_bytes_exclusive(asset.target, asset.source.read_bytes(), root)


def _write_bytes_exclusive(
    target: Path,
    content: bytes,
    root: BoundRoot,
) -> CreatedFile:
    parent_fd = _open_parent_at(root, target, create=True)
    stage_name = ""
    staged: FileSnapshot | None = None
    try:
        stage_name, staged = _stage_bytes_at(
            parent_fd,
            target.parent,
            target.name,
            content,
        )
        published_stage = stage_name
        stage_name = ""
        installed = _publish_staged_at(
            parent_fd,
            target.parent,
            published_stage,
            staged,
            target.name,
        )
        return CreatedFile(target, parent_fd, target.name, installed)
    except Exception as error:
        recovery_paths = list(getattr(error, "recovery_paths", ()))
        if stage_name and staged is not None:
            try:
                recovery_paths.extend(
                    _remove_expected_entry(
                        parent_fd,
                        target.parent,
                        stage_name,
                        staged,
                        purpose="failed-stage",
                    )
                )
            except PublicationConflict as cleanup_error:
                recovery_paths.extend(cleanup_error.recovery_paths)
            except OSError:
                recovery_paths.append(str(target.parent / stage_name))
        try:
            os.close(parent_fd)
        finally:
            raise PublicationConflict(
                str(error),
                recovery_paths=tuple(sorted(set(recovery_paths))),
            ) from error


def _retire_backup_or_restore(
    parent_fd: int,
    parent: Path,
    target_name: str,
    backup_name: str,
    backup: FileSnapshot,
) -> list[str]:
    if not _entry_exists(parent_fd, target_name):
        try:
            _rename_no_replace_at(parent_fd, backup_name, target_name)
            return []
        except FileExistsError:
            pass
    return _remove_expected_entry(
        parent_fd,
        parent,
        backup_name,
        backup,
        purpose="rollback-backup",
    )


def _replace_managed_file(
    target: Path,
    content: bytes,
    expected: FileSnapshot,
    root: BoundRoot,
) -> ReplacedFile:
    parent_fd = _open_parent_at(root, target, create=False)
    stage_name = ""
    staged: FileSnapshot | None = None
    backup_name = ""
    backup: FileSnapshot | None = None
    moved = False
    try:
        stage_name, staged = _stage_bytes_at(
            parent_fd,
            target.parent,
            target.name,
            content,
        )
        backup_name = _unique_entry_name(parent_fd, f"{target.name}.backup")
        current, _ = _snapshot_at(parent_fd, target.name)
        if not _same_snapshot(current, expected):
            raise ValueError("managed file changed before upgrade")
        _rename_no_replace_at(parent_fd, target.name, backup_name)
        moved = True
        backup, _ = _snapshot_at(parent_fd, backup_name)
        if not _same_snapshot(backup, expected):
            raise ValueError("managed file changed during upgrade")
        published_stage = stage_name
        stage_name = ""
        installed = _publish_staged_at(
            parent_fd,
            target.parent,
            published_stage,
            staged,
            target.name,
        )
        return ReplacedFile(
            path=target,
            parent_fd=parent_fd,
            name=target.name,
            installed=installed,
            backup_name=backup_name,
            backup=backup,
        )
    except Exception as error:
        recovery_paths = list(getattr(error, "recovery_paths", ()))
        if backup is None and backup_name and _entry_matches_snapshot(
            parent_fd,
            backup_name,
            expected,
        ):
            backup = expected
            moved = True
        if moved and backup is not None:
            try:
                recovery_paths.extend(
                    _retire_backup_or_restore(
                        parent_fd,
                        target.parent,
                        target.name,
                        backup_name,
                        backup,
                    )
                )
            except PublicationConflict as cleanup_error:
                recovery_paths.extend(cleanup_error.recovery_paths)
            except OSError:
                recovery_paths.append(str(target.parent / backup_name))
        if stage_name and staged is not None:
            try:
                recovery_paths.extend(
                    _remove_expected_entry(
                        parent_fd,
                        target.parent,
                        stage_name,
                        staged,
                        purpose="failed-stage",
                    )
                )
            except PublicationConflict as cleanup_error:
                recovery_paths.extend(cleanup_error.recovery_paths)
            except OSError:
                recovery_paths.append(str(target.parent / stage_name))
        try:
            os.close(parent_fd)
        finally:
            raise PublicationConflict(
                str(error),
                recovery_paths=tuple(sorted(set(recovery_paths))),
            ) from error


def _rollback_replaced_files(replaced: list[ReplacedFile]) -> list[str]:
    recovery_paths: list[str] = []
    for item in reversed(replaced):
        try:
            recovery_paths.extend(
                _remove_expected_entry(
                    item.parent_fd,
                    item.path.parent,
                    item.name,
                    item.installed,
                    purpose="rollback-publication",
                )
            )
        except PublicationConflict as error:
            recovery_paths.extend(error.recovery_paths)
        except OSError:
            recovery_paths.append(str(item.path))
        try:
            recovery_paths.extend(
                _retire_backup_or_restore(
                    item.parent_fd,
                    item.path.parent,
                    item.name,
                    item.backup_name,
                    item.backup,
                )
            )
        except PublicationConflict as error:
            recovery_paths.extend(error.recovery_paths)
        except OSError:
            recovery_paths.append(str(item.path.parent / item.backup_name))
        finally:
            try:
                os.close(item.parent_fd)
            except OSError:
                recovery_paths.append(str(item.path.parent))
    return recovery_paths


def _remove_created_files(created: list[CreatedFile]) -> list[str]:
    recovery_paths: list[str] = []
    for item in reversed(created):
        try:
            recovery_paths.extend(
                _remove_expected_entry(
                    item.parent_fd,
                    item.path.parent,
                    item.name,
                    item.installed,
                    purpose="rollback-created",
                )
            )
        except PublicationConflict as error:
            recovery_paths.extend(error.recovery_paths)
        except OSError:
            recovery_paths.append(str(item.path))
        finally:
            try:
                os.close(item.parent_fd)
            except OSError:
                recovery_paths.append(str(item.path.parent))
    return recovery_paths


def _close_bound_root(root: BoundRoot, *, rollback: bool) -> None:
    for item in root.created_directories:
        with suppress(OSError):
            os.close(item.parent_fd)
    try:
        os.close(root.fd)
    finally:
        os.close(root.parent_fd)


def _rollback_asset_transaction(transaction: AssetTransaction) -> list[str]:
    recovery_paths: list[str] = list(transaction.root.recovery_paths)
    try:
        try:
            recovery_paths.extend(_rollback_replaced_files(transaction.replaced))
        except Exception:
            recovery_paths.append(str(transaction.root.home))
        try:
            recovery_paths.extend(_remove_created_files(transaction.created))
        except Exception:
            recovery_paths.append(str(transaction.root.home))
    finally:
        try:
            _close_bound_root(transaction.root, rollback=True)
        except OSError:
            recovery_paths.append(str(transaction.root.home))
    return sorted(set(recovery_paths))


def _finish_asset_transaction(transaction: AssetTransaction) -> list[str]:
    cleanup_pending: list[str] = list(transaction.root.recovery_paths)
    for replaced_file in transaction.replaced:
        try:
            cleanup_pending.extend(
                _remove_expected_entry(
                    replaced_file.parent_fd,
                    replaced_file.path.parent,
                    replaced_file.backup_name,
                    replaced_file.backup,
                    purpose="cleanup-tombstone",
                )
            )
        except PublicationConflict as error:
            cleanup_pending.extend(error.recovery_paths)
        except OSError:
            cleanup_pending.append(
                str(replaced_file.path.parent / replaced_file.backup_name)
            )
        finally:
            with suppress(OSError):
                os.close(replaced_file.parent_fd)
    for created_file in transaction.created:
        with suppress(OSError):
            os.close(created_file.parent_fd)
    try:
        _close_bound_root(transaction.root, rollback=False)
    except OSError:
        cleanup_pending.append(str(transaction.root.home))
    return _home_relative_paths(transaction.root.home, cleanup_pending)


def _fsync_asset_transaction(transaction: AssetTransaction) -> None:
    descriptors = {item.parent_fd for item in transaction.created}
    descriptors.update(item.parent_fd for item in transaction.replaced)
    descriptors.update(
        item.parent_fd for item in transaction.root.created_directories
    )
    descriptors.add(transaction.root.fd)
    for descriptor in descriptors:
        os.fsync(descriptor)


def _home_relative_paths(home: Path, paths: list[str]) -> list[str]:
    normalized: set[str] = set()
    for value in paths:
        path = Path(value)
        try:
            normalized.add(path.relative_to(home).as_posix())
        except ValueError:
            normalized.add(path.as_posix())
    return sorted(normalized)


def _manifest_bytes(assets: dict[str, str]) -> bytes:
    payload = {
        "schema_version": OWNERSHIP_SCHEMA_VERSION,
        "assets": dict(sorted(assets.items())),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _recheck_asset_preimages(
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    root: BoundRoot,
) -> None:
    for asset in assets:
        state = states[asset.target]
        parent_fd = _open_parent_at(root, asset.target, create=state.status == "missing")
        try:
            if state.status == "missing":
                if _entry_exists(parent_fd, asset.target.name):
                    raise ValueError("managed asset appeared before publication")
                continue
            if state.live is None:
                raise ValueError("managed asset has no preimage")
            current, _ = _snapshot_at(parent_fd, asset.target.name)
            if not _same_snapshot(current, state.live):
                raise ValueError("managed asset changed before publication")
            _assert_parent_current(parent_fd, asset.target.parent)
        finally:
            os.close(parent_fd)


def _recheck_published_assets(
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    transaction: AssetTransaction,
) -> None:
    created = {item.path: item for item in transaction.created}
    replaced = {item.path: item for item in transaction.replaced}
    for asset in assets:
        state = states[asset.target]
        installed = created.get(asset.target) or replaced.get(asset.target)
        if installed is not None:
            parent_fd = installed.parent_fd
            name = installed.name
            close_parent = False
        else:
            parent_fd = _open_parent_at(transaction.root, asset.target, create=False)
            name = asset.target.name
            close_parent = True
        try:
            current, _ = _snapshot_at(parent_fd, name)
            _assert_parent_current(parent_fd, asset.target.parent)
        finally:
            if close_parent:
                os.close(parent_fd)
        if current.sha256 != state.source_sha256:
            raise ValueError("managed asset changed during publication")
        if state.status == "identical":
            if state.live is None or not _same_snapshot(current, state.live):
                raise ValueError("managed asset changed during publication")
        elif state.status == "missing":
            installed = created.get(asset.target)
            if installed is None or not _same_snapshot(current, installed.installed):
                raise ValueError("managed asset changed during publication")
        elif state.status == "upgrade":
            installed = replaced.get(asset.target)
            if installed is None or not _same_snapshot(current, installed.installed):
                raise ValueError("managed asset changed during publication")


def _recheck_manifest_preimage(
    ownership: OwnershipManifest,
    root: BoundRoot,
) -> bytes | None:
    parent_fd = _open_parent_at(root, ownership.path, create=ownership.status == "missing")
    try:
        if ownership.status == "missing":
            if _entry_exists(parent_fd, ownership.path.name):
                raise ValueError("ownership manifest appeared before publication")
            return None
        if ownership.status != "valid" or ownership.snapshot is None:
            raise ValueError("ownership manifest is invalid")
        current, raw = _snapshot_at(parent_fd, ownership.path.name)
        if not _same_snapshot(current, ownership.snapshot):
            raise ValueError("ownership manifest changed before publication")
        _assert_parent_current(parent_fd, ownership.path.parent)
        return raw
    finally:
        os.close(parent_fd)


def _recheck_published_manifest(
    ownership: OwnershipManifest,
    desired: bytes,
    transaction: AssetTransaction,
) -> None:
    created = next(
        (item for item in transaction.created if item.path == ownership.path),
        None,
    )
    replaced = next(
        (item for item in transaction.replaced if item.path == ownership.path),
        None,
    )
    installed = created or replaced
    if installed is not None:
        parent_fd = installed.parent_fd
        name = installed.name
        close_parent = False
    else:
        parent_fd = _open_parent_at(transaction.root, ownership.path, create=False)
        name = ownership.path.name
        close_parent = True
    try:
        current, raw = _snapshot_at(parent_fd, name)
        _assert_parent_current(parent_fd, ownership.path.parent)
    finally:
        if close_parent:
            os.close(parent_fd)
    if raw != desired:
        raise ValueError("ownership manifest changed during publication")
    if created is not None and not _same_snapshot(current, created.installed):
        raise ValueError("ownership manifest changed during publication")
    if replaced is not None and not _same_snapshot(current, replaced.installed):
        raise ValueError("ownership manifest changed during publication")
    if (
        created is None
        and replaced is None
        and (
            ownership.snapshot is None
            or not _same_snapshot(current, ownership.snapshot)
        )
    ):
        raise ValueError("ownership manifest changed during publication")


def _apply_asset_changes(
    *,
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    ownership: OwnershipManifest,
    home: Path,
) -> AssetTransaction:
    root = _open_bound_root(home)
    transaction = AssetTransaction(created=[], replaced=[], root=root)
    try:
        _assert_root_current(root)
        _recheck_asset_preimages(assets, states, root)
        current_manifest = _recheck_manifest_preimage(ownership, root)
        for asset in assets:
            state = states[asset.target]
            if state.status == "missing":
                transaction.created.append(_write_asset(asset, root))
            elif state.status == "upgrade":
                if state.live is None:
                    raise ValueError("managed upgrade has no preimage")
                transaction.replaced.append(
                    _replace_managed_file(
                        asset.target,
                        asset.source.read_bytes(),
                        state.live,
                        root,
                    )
                )

        _recheck_published_assets(assets, states, transaction)
        manifest_assets = dict(ownership.assets)
        manifest_assets.update(
            {
                _target_relative(asset.target, home): states[asset.target].source_sha256
                for asset in assets
            }
        )
        desired_manifest = _manifest_bytes(manifest_assets)
        if ownership.status == "missing":
            transaction.created.append(
                _write_bytes_exclusive(ownership.path, desired_manifest, root)
            )
        elif ownership.status == "valid":
            if current_manifest != desired_manifest:
                if ownership.snapshot is None:
                    raise ValueError("valid ownership manifest has no preimage")
                transaction.replaced.append(
                    _replace_managed_file(
                        ownership.path,
                        desired_manifest,
                        ownership.snapshot,
                        root,
                    )
                )
        else:
            raise ValueError("ownership manifest is invalid")
        _recheck_published_manifest(ownership, desired_manifest, transaction)
        _assert_root_current(root)
        _fsync_asset_transaction(transaction)
    except Exception as error:
        recovery_paths = _rollback_asset_transaction(transaction)
        if isinstance(error, PublicationConflict):
            recovery_paths.extend(error.recovery_paths)
        normalized = _home_relative_paths(home, recovery_paths)
        raise PublicationConflict(
            str(error),
            recovery_paths=tuple(normalized),
        ) from error
    return transaction


def _operation_payload(
    assets: tuple[Asset, ...],
    clients: tuple[Client, ...],
    states: dict[Path, AssetState],
    *,
    include_mcp: bool,
) -> list[AssistantSetupOperation]:
    operations: list[AssistantSetupOperation] = [
        AssistantSetupOperation(
            kind="copy",
            target=str(asset.target),
            status=states[asset.target].status,
        )
        for asset in assets
    ]
    if include_mcp:
        operations.extend(
            AssistantSetupOperation(
                kind="mcp",
                client=client,
                name=MCP_NAME,
                status="planned",
            )
            for client in clients
        )
    return operations


def _run_assets_only(
    *,
    clients: tuple[Client, ...],
    mode: Mode,
    home: Path,
) -> tuple[AssistantSetupPayload, int]:
    assets = _assets(home, clients)
    ownership = _ownership_manifest(home)
    states = {
        asset.target: _asset_state(asset, home, ownership) for asset in assets
    }
    operations = _operation_payload(
        assets,
        clients,
        states,
        include_mcp=False,
    )
    base: AssistantSetupPayload = {
        "mode": mode,
        "clients": list(clients),
        "assets_only": True,
        "operations": operations,
    }
    conflicts = [
        str(path) for path, state in states.items() if state.status == "conflict"
    ]
    if ownership.status == "conflict":
        conflicts.insert(0, str(ownership.path))

    if mode == "dry-run":
        base["status"] = "blocked" if conflicts else "planned"
        base["conflicts"] = conflicts
        return base, 1 if conflicts else 0

    if mode == "verify":
        invalid_assets = [
            str(path)
            for path, state in states.items()
            if state.status != "identical"
        ]
        manifest_valid = ownership.status == "valid" and all(
            ownership.assets.get(_target_relative(asset.target, home))
            == states[asset.target].source_sha256
            for asset in assets
        )
        valid = not invalid_assets and manifest_valid
        base["status"] = "verified" if valid else "invalid"
        base["invalid_assets"] = invalid_assets
        base["invalid_manifest"] = (
            None if manifest_valid else str(ownership.path)
        )
        return base, 0 if valid else 1

    if conflicts:
        base["status"] = "blocked"
        base["conflicts"] = conflicts
        return base, 1

    try:
        transaction = _apply_asset_changes(
            assets=assets,
            states=states,
            ownership=ownership,
            home=home,
        )
    except Exception as error:
        recovery_paths = _home_relative_paths(
            home,
            list(getattr(error, "recovery_paths", ())),
        )
        base["status"] = "rolled_back"
        base["error"] = str(error)
        base["recovery_paths"] = recovery_paths
        return base, 1

    cleanup_pending = _finish_asset_transaction(transaction)
    base["status"] = "installed"
    base["created_files"] = len(transaction.created)
    base["upgraded_files"] = sum(
        state.status == "upgrade" for state in states.values()
    )
    base["cleanup_pending"] = cleanup_pending
    return base, 0


def run_assistant_setup(
    *,
    client: str,
    data_root: Path | None,
    mcp_command: str,
    mode: Mode,
    assets_only: bool = False,
    home: Path | None = None,
) -> tuple[AssistantSetupPayload, int]:
    """Plan, apply, or verify the assistant integration.

    The returned integer is a process-style status code. Errors are deliberately
    bounded and never include client stderr or environment values.
    """
    clients = resolve_clients(client)
    target_home = (home or Path.home()).resolve()
    if assets_only:
        return _run_assets_only(clients=clients, mode=mode, home=target_home)

    if data_root is None:
        raise ValueError("data root is required unless --assets-only is used")
    if not data_root.is_absolute():
        raise ValueError("data root must be an absolute path")
    server = _resolve_executable(mcp_command, label="MCP server")
    executables = _client_executables(clients)
    assets = _assets(target_home, clients)
    ownership = _ownership_manifest(target_home)
    states = {
        asset.target: _asset_state(asset, target_home, ownership) for asset in assets
    }
    operations = _operation_payload(assets, clients, states, include_mcp=True)
    base: AssistantSetupPayload = {
        "mode": mode,
        "clients": list(clients),
        "assets_only": False,
        "data_root": str(data_root),
        "mcp_command": str(server),
        "operations": operations,
    }

    if mode == "dry-run":
        conflicts = [
            str(path) for path, state in states.items() if state.status == "conflict"
        ]
        if ownership.status == "conflict":
            conflicts.insert(0, str(ownership.path))
        base["status"] = "blocked" if conflicts else "planned"
        base["conflicts"] = conflicts
        return base, 1 if conflicts else 0

    conflicts = [
        str(path) for path, state in states.items() if state.status == "conflict"
    ]
    if ownership.status == "conflict":
        conflicts.insert(0, str(ownership.path))
    if mode == "apply" and conflicts:
        base["status"] = "blocked"
        base["conflicts"] = conflicts
        base["existing_clients"] = []
        return base, 1

    registrations = {
        name: _get_registration(executable)
        for name, executable in executables.items()
    }
    if mode == "verify":
        invalid_assets = [
            str(path)
            for path, state in states.items()
            if state.status != "identical"
        ]
        manifest_valid = ownership.status == "valid" and all(
            ownership.assets.get(_target_relative(asset.target, target_home))
            == states[asset.target].source_sha256
            for asset in assets
        )
        missing_clients = [name for name, registered in registrations.items() if not registered]
        valid = not invalid_assets and manifest_valid and not missing_clients
        base["status"] = "verified" if valid else "invalid"
        base["invalid_assets"] = invalid_assets
        base["invalid_manifest"] = (
            None if manifest_valid else str(ownership.path)
        )
        base["missing_clients"] = missing_clients
        return base, 0 if valid else 1

    existing_clients = [name for name, registered in registrations.items() if registered]
    if conflicts or existing_clients:
        base["status"] = "blocked"
        base["conflicts"] = conflicts
        base["existing_clients"] = existing_clients
        return base, 1

    transaction: AssetTransaction | None = None
    registered: list[Client] = []
    attempted: list[Client] = []
    try:
        transaction = _apply_asset_changes(
            assets=assets,
            states=states,
            ownership=ownership,
            home=target_home,
        )
        for name in clients:
            attempted.append(name)
            result = subprocess.run(
                _registration_command(name, executables[name], server, data_root),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{name} MCP registration failed with exit code {result.returncode}"
                )
            registered.append(name)
    except Exception as error:
        failed_client_cleanup: list[AssistantSetupCleanupFailure] = []
        recovery_paths: list[str] = []
        try:
            for name in reversed(attempted):
                try:
                    _remove_registration(name, executables[name])
                except Exception as cleanup_error:
                    failed_client_cleanup.append(
                        {
                            "client": name,
                            "error": type(cleanup_error).__name__,
                        }
                    )
        finally:
            if transaction is not None:
                recovery_paths.extend(_rollback_asset_transaction(transaction))
        if isinstance(error, PublicationConflict):
            recovery_paths.extend(error.recovery_paths)
        recovery_paths = _home_relative_paths(target_home, recovery_paths)
        base["status"] = "rolled_back"
        base["error"] = str(error)
        base["recovery_paths"] = sorted(set(recovery_paths))
        base["failed_client_cleanup"] = sorted(
            failed_client_cleanup,
            key=lambda item: (item["client"], item["error"]),
        )
        return base, 1

    cleanup_pending = _finish_asset_transaction(transaction)
    base["status"] = "installed"
    base["created_files"] = len(transaction.created)
    base["upgraded_files"] = sum(
        state.status == "upgrade" for state in states.values()
    )
    base["registered_clients"] = list(registered)
    base["cleanup_pending"] = cleanup_pending
    return base, 0
