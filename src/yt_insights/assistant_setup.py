"""Safe installer for the optional Claude Code and Codex integration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Literal


Client = Literal["claude", "codex"]
Mode = Literal["dry-run", "apply", "verify"]
MCP_NAME = "yt-insights"
OWNERSHIP_MANIFEST_RELATIVE = Path(".agents/.yt-insights-assistant-assets-v1.json")
OWNERSHIP_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


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
    installed: FileSnapshot
    backup: Path


@dataclass
class AssetTransaction:
    created: list[CreatedFile]
    replaced: list[ReplacedFile]


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
    subprocess.run(command, check=False, capture_output=True, text=True)


def _write_asset(asset: Asset) -> CreatedFile:
    return _write_bytes_exclusive(asset.target, asset.source.read_bytes())


def _write_bytes_exclusive(target: Path, content: bytes) -> CreatedFile:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, target)
        installed, _ = _snapshot_file(target)
        return CreatedFile(target, installed)
    finally:
        temporary.unlink(missing_ok=True)


def _stage_bytes(target: Path, content: bytes, *, suffix: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.{suffix}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _reserve_backup_path(target: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.backup.", dir=target.parent
    )
    os.close(descriptor)
    backup = Path(temporary_name)
    backup.unlink()
    return backup


def _restore_backup_without_overwrite(backup: Path, target: Path) -> bool:
    if target.exists() or target.is_symlink():
        return False
    os.link(backup, target)
    return True


def _replace_managed_file(
    target: Path,
    content: bytes,
    expected: FileSnapshot,
) -> ReplacedFile:
    staged = _stage_bytes(target, content, suffix="new")
    backup = _reserve_backup_path(target)
    moved = False
    try:
        current, _ = _snapshot_file(target)
        if not _same_snapshot(current, expected):
            raise ValueError("managed file changed before upgrade")
        os.replace(target, backup)
        moved = True
        backup_snapshot, _ = _snapshot_file(backup)
        if not _same_snapshot(backup_snapshot, expected):
            _restore_backup_without_overwrite(backup, target)
            raise ValueError("managed file changed during upgrade")
        os.link(staged, target)
        installed, _ = _snapshot_file(target)
        return ReplacedFile(
            path=target,
            installed=installed,
            backup=backup,
        )
    except Exception:
        if moved:
            restored = _restore_backup_without_overwrite(backup, target)
            if restored:
                backup.unlink(missing_ok=True)
        raise
    finally:
        staged.unlink(missing_ok=True)
        if not moved:
            backup.unlink(missing_ok=True)


def _rollback_replaced_files(replaced: list[ReplacedFile]) -> None:
    for item in reversed(replaced):
        try:
            current, _ = _snapshot_file(item.path)
        except FileNotFoundError:
            current = None
        except (OSError, ValueError):
            continue
        if current is not None and not _same_snapshot(current, item.installed):
            continue
        if current is not None:
            item.path.unlink()
        if _restore_backup_without_overwrite(item.backup, item.path):
            item.backup.unlink(missing_ok=True)


def _rollback_asset_transaction(transaction: AssetTransaction, home: Path) -> None:
    _rollback_replaced_files(transaction.replaced)
    _remove_created_files(transaction.created, home)


def _finish_asset_transaction(transaction: AssetTransaction) -> None:
    for item in transaction.replaced:
        item.backup.unlink(missing_ok=True)


def _manifest_bytes(assets: dict[str, str]) -> bytes:
    payload = {
        "schema_version": OWNERSHIP_SCHEMA_VERSION,
        "assets": dict(sorted(assets.items())),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _recheck_asset_preimages(
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    home: Path,
) -> None:
    for asset in assets:
        state = states[asset.target]
        if _has_unsafe_parent(asset.target, home) or asset.target.is_symlink():
            raise ValueError("managed asset path changed before publication")
        if state.status == "missing":
            if asset.target.exists():
                raise ValueError("managed asset appeared before publication")
            continue
        if state.live is None:
            raise ValueError("managed asset has no preimage")
        current, _ = _snapshot_file(asset.target)
        if not _same_snapshot(current, state.live):
            raise ValueError("managed asset changed before publication")


def _recheck_published_assets(
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    transaction: AssetTransaction,
) -> None:
    created = {item.path: item for item in transaction.created}
    replaced = {item.path: item for item in transaction.replaced}
    for asset in assets:
        state = states[asset.target]
        current, _ = _snapshot_file(asset.target)
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


def _recheck_manifest_preimage(ownership: OwnershipManifest) -> bytes | None:
    if ownership.status == "missing":
        if ownership.path.exists() or ownership.path.is_symlink():
            raise ValueError("ownership manifest appeared before publication")
        return None
    if ownership.status != "valid" or ownership.snapshot is None:
        raise ValueError("ownership manifest is invalid")
    current, raw = _snapshot_file(ownership.path)
    if not _same_snapshot(current, ownership.snapshot):
        raise ValueError("ownership manifest changed before publication")
    return raw


def _recheck_published_manifest(
    ownership: OwnershipManifest,
    desired: bytes,
    transaction: AssetTransaction,
) -> None:
    current, raw = _snapshot_file(ownership.path)
    if raw != desired:
        raise ValueError("ownership manifest changed during publication")
    created = next(
        (item for item in transaction.created if item.path == ownership.path),
        None,
    )
    replaced = next(
        (item for item in transaction.replaced if item.path == ownership.path),
        None,
    )
    if created is not None and not _same_snapshot(current, created.installed):
        raise ValueError("ownership manifest changed during publication")
    if replaced is not None and not _same_snapshot(current, replaced.installed):
        raise ValueError("ownership manifest changed during publication")
    if created is None and replaced is None:
        if ownership.snapshot is None or not _same_snapshot(current, ownership.snapshot):
            raise ValueError("ownership manifest changed during publication")


def _apply_asset_changes(
    *,
    assets: tuple[Asset, ...],
    states: dict[Path, AssetState],
    ownership: OwnershipManifest,
    home: Path,
) -> AssetTransaction:
    transaction = AssetTransaction(created=[], replaced=[])
    try:
        _recheck_asset_preimages(assets, states, home)
        current_manifest = _recheck_manifest_preimage(ownership)
        for asset in assets:
            state = states[asset.target]
            if state.status == "missing":
                transaction.created.append(_write_asset(asset))
            elif state.status == "upgrade":
                if state.live is None:
                    raise ValueError("managed upgrade has no preimage")
                transaction.replaced.append(
                    _replace_managed_file(
                        asset.target,
                        asset.source.read_bytes(),
                        state.live,
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
                _write_bytes_exclusive(ownership.path, desired_manifest)
            )
        elif ownership.status == "valid":
            if current_manifest != desired_manifest:
                transaction.replaced.append(
                    _replace_managed_file(
                        ownership.path,
                        desired_manifest,
                        ownership.snapshot,
                    )
                )
        else:
            raise ValueError("ownership manifest is invalid")
        _recheck_published_manifest(ownership, desired_manifest, transaction)
    except Exception:
        _rollback_asset_transaction(transaction, home)
        raise
    return transaction


def _remove_created_files(created: list[CreatedFile], home: Path) -> None:
    for item in reversed(created):
        target = item.path
        try:
            current, _ = _snapshot_file(target)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            continue
        if not _same_snapshot(current, item.installed):
            continue
        target.unlink()
        parent = target.parent
        while parent != home and home in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _operation_payload(
    assets: tuple[Asset, ...],
    clients: tuple[Client, ...],
    states: dict[Path, AssetState],
    *,
    include_mcp: bool,
) -> list[dict[str, str]]:
    operations = [
        {
            "kind": "copy",
            "target": str(asset.target),
            "status": states[asset.target].status,
        }
        for asset in assets
    ]
    if include_mcp:
        operations.extend(
            {"kind": "mcp", "client": client, "name": MCP_NAME, "status": "planned"}
            for client in clients
        )
    return operations


def _run_assets_only(
    *,
    clients: tuple[Client, ...],
    mode: Mode,
    home: Path,
) -> tuple[dict[str, object], int]:
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
    base: dict[str, object] = {
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
        base.update(status="blocked" if conflicts else "planned", conflicts=conflicts)
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
        base.update(
            status="verified" if valid else "invalid",
            invalid_assets=invalid_assets,
            invalid_manifest=None if manifest_valid else str(ownership.path),
        )
        return base, 0 if valid else 1

    if conflicts:
        base.update(status="blocked", conflicts=conflicts)
        return base, 1

    try:
        transaction = _apply_asset_changes(
            assets=assets,
            states=states,
            ownership=ownership,
            home=home,
        )
    except Exception as error:
        base.update(status="rolled_back", error=str(error))
        return base, 1

    _finish_asset_transaction(transaction)
    base.update(
        status="installed",
        created_files=len(transaction.created),
        upgraded_files=sum(state.status == "upgrade" for state in states.values()),
    )
    return base, 0


def run_assistant_setup(
    *,
    client: str,
    data_root: Path | None,
    mcp_command: str,
    mode: Mode,
    assets_only: bool = False,
    home: Path | None = None,
) -> tuple[dict[str, object], int]:
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
    base: dict[str, object] = {
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
        base.update(status="blocked" if conflicts else "planned", conflicts=conflicts)
        return base, 1 if conflicts else 0

    conflicts = [
        str(path) for path, state in states.items() if state.status == "conflict"
    ]
    if ownership.status == "conflict":
        conflicts.insert(0, str(ownership.path))
    if mode == "apply" and conflicts:
        base.update(status="blocked", conflicts=conflicts, existing_clients=[])
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
        base.update(
            status="verified" if valid else "invalid",
            invalid_assets=invalid_assets,
            invalid_manifest=None if manifest_valid else str(ownership.path),
            missing_clients=missing_clients,
        )
        return base, 0 if valid else 1

    existing_clients = [name for name, registered in registrations.items() if registered]
    if conflicts or existing_clients:
        base.update(status="blocked", conflicts=conflicts, existing_clients=existing_clients)
        return base, 1

    transaction = AssetTransaction(created=[], replaced=[])
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
        for name in reversed(attempted):
            _remove_registration(name, executables[name])
        _rollback_asset_transaction(transaction, target_home)
        base.update(status="rolled_back", error=str(error))
        return base, 1

    _finish_asset_transaction(transaction)
    base.update(
        status="installed",
        created_files=len(transaction.created),
        upgraded_files=sum(state.status == "upgrade" for state in states.values()),
        registered_clients=list(registered),
    )
    return base, 0
