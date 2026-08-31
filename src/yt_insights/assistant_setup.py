"""Safe installer for the optional Claude Code and Codex integration."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal


Client = Literal["claude", "codex"]
Mode = Literal["dry-run", "apply", "verify"]
MCP_NAME = "yt-insights"


@dataclass(frozen=True)
class Asset:
    source: Path
    target: Path


@dataclass(frozen=True)
class CreatedFile:
    path: Path
    device: int
    inode: int


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


def _file_state(asset: Asset, home: Path) -> str:
    if _has_unsafe_parent(asset.target, home):
        return "conflict"
    if asset.target.is_symlink():
        return "conflict"
    if not asset.target.exists():
        return "missing"
    if not asset.target.is_file():
        return "conflict"
    return "identical" if asset.target.read_bytes() == asset.source.read_bytes() else "conflict"


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
    asset.target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{asset.target.name}.", dir=asset.target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(asset.source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.link(temporary, asset.target)
        published = asset.target.stat()
        return CreatedFile(asset.target, published.st_dev, published.st_ino)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_created_files(created: list[CreatedFile], home: Path) -> None:
    for item in reversed(created):
        target = item.path
        try:
            current = target.lstat()
        except FileNotFoundError:
            continue
        if target.is_symlink() or (current.st_dev, current.st_ino) != (
            item.device,
            item.inode,
        ):
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
    states: dict[Path, str],
    *,
    include_mcp: bool,
) -> list[dict[str, str]]:
    operations = [
        {"kind": "copy", "target": str(asset.target), "status": states[asset.target]}
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
    states = {asset.target: _file_state(asset, home) for asset in assets}
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
    conflicts = [str(path) for path, state in states.items() if state == "conflict"]

    if mode == "dry-run":
        base.update(status="blocked" if conflicts else "planned", conflicts=conflicts)
        return base, 1 if conflicts else 0

    if mode == "verify":
        invalid_assets = [str(path) for path, state in states.items() if state != "identical"]
        valid = not invalid_assets
        base.update(
            status="verified" if valid else "invalid",
            invalid_assets=invalid_assets,
        )
        return base, 0 if valid else 1

    if conflicts:
        base.update(status="blocked", conflicts=conflicts)
        return base, 1

    created: list[CreatedFile] = []
    try:
        for asset in assets:
            if states[asset.target] == "missing":
                created.append(_write_asset(asset))
    except Exception as error:
        _remove_created_files(created, home)
        base.update(status="rolled_back", error=str(error))
        return base, 1

    base.update(status="installed", created_files=len(created))
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
    states = {asset.target: _file_state(asset, target_home) for asset in assets}
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
        conflicts = [str(path) for path, state in states.items() if state == "conflict"]
        base.update(status="blocked" if conflicts else "planned", conflicts=conflicts)
        return base, 1 if conflicts else 0

    conflicts = [str(path) for path, state in states.items() if state == "conflict"]
    if mode == "apply" and conflicts:
        base.update(status="blocked", conflicts=conflicts, existing_clients=[])
        return base, 1

    registrations = {
        name: _get_registration(executable)
        for name, executable in executables.items()
    }
    if mode == "verify":
        invalid_assets = [str(path) for path, state in states.items() if state != "identical"]
        missing_clients = [name for name, registered in registrations.items() if not registered]
        valid = not invalid_assets and not missing_clients
        base.update(
            status="verified" if valid else "invalid",
            invalid_assets=invalid_assets,
            missing_clients=missing_clients,
        )
        return base, 0 if valid else 1

    existing_clients = [name for name, registered in registrations.items() if registered]
    if conflicts or existing_clients:
        base.update(status="blocked", conflicts=conflicts, existing_clients=existing_clients)
        return base, 1

    created: list[CreatedFile] = []
    registered: list[Client] = []
    attempted: list[Client] = []
    try:
        for asset in assets:
            if states[asset.target] == "missing":
                created.append(_write_asset(asset))
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
        _remove_created_files(created, target_home)
        base.update(status="rolled_back", error=str(error))
        return base, 1

    base.update(
        status="installed",
        created_files=len(created),
        registered_clients=list(registered),
    )
    return base, 0
