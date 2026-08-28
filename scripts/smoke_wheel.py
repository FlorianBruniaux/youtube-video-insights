#!/usr/bin/env python3
"""Build and smoke-test the installable wheel outside the source checkout."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
from zipfile import ZipFile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = "0.2.0"


class SmokeFailure(RuntimeError):
    """Report one failed command with its captured output."""


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    expected_code: int = 0,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != expected_code:
        rendered = " ".join(command)
        raise SmokeFailure(
            f"Command exited {result.returncode}, expected {expected_code}: {rendered}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _uv_command(uv: str, subcommand: str, *, offline: bool) -> list[str]:
    command = [uv, subcommand]
    if offline:
        command.append("--offline")
    return command


def _create_environment(
    uv: str,
    destination: Path,
    wheel_requirement: str,
    *,
    offline: bool,
    environment: dict[str, str],
) -> Path:
    _run(
        [uv, "venv", str(destination), "--python", sys.executable],
        cwd=destination.parent,
        environment=environment,
    )
    python = destination / "bin" / "python"
    install = _uv_command(uv, "pip", offline=offline)
    install.extend(["install", "--python", str(python), wheel_requirement])
    _run(install, cwd=destination.parent, environment=environment)
    return python


def _script(venv: Path, name: str) -> Path:
    return venv / "bin" / name


def _copy_build_source(workspace: Path) -> Path:
    """Copy only current package inputs into a temporary clean build tree."""
    destination = workspace / "build-source"
    destination.mkdir()
    for filename in ("pyproject.toml", "README.md", "LICENSE"):
        source = REPOSITORY_ROOT / filename
        if source.is_file():
            shutil.copy2(source, destination / filename)
    shutil.copytree(
        REPOSITORY_ROOT / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("*.egg-info", "__pycache__", "*.pyc", "*.pyo"),
    )
    return destination


@contextmanager
def _stale_build_sentinel():
    """Plant one stale build file without overwriting or deleting user data."""
    build = REPOSITORY_ROOT / "build"
    library = build / "lib"
    package = library / "yt_insights"
    directories = (build, library, package)
    existed = {directory: directory.exists() for directory in directories}
    package.mkdir(parents=True, exist_ok=True)
    sentinel = package / f"_stale_smoke_{uuid.uuid4().hex}.py"
    content = b'raise RuntimeError("stale checkout build cache leaked into wheel")\n'
    sentinel.write_bytes(content)
    try:
        yield sentinel.name
    finally:
        if sentinel.exists():
            if sentinel.read_bytes() != content:
                raise SmokeFailure(
                    f"Stale sentinel changed during the smoke and was preserved: {sentinel}"
                )
            sentinel.unlink()
        for directory in reversed(directories):
            if not existed[directory]:
                try:
                    directory.rmdir()
                except OSError:
                    pass


def _verify_wheel(
    wheel: Path, build_source: Path, *, stale_sentinel: str
) -> int:
    """Verify current modules and RECORD without trusting setuptools build caches."""
    expected_modules = {
        source.relative_to(build_source / "src").as_posix()
        for source in (build_source / "src" / "yt_insights").rglob("*.py")
    }
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
        record_names = [name for name in members if name.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise SmokeFailure(f"Expected one wheel RECORD, found {len(record_names)}.")
        record = archive.read(record_names[0]).decode("utf-8")

    missing = sorted(expected_modules - members)
    if missing:
        raise SmokeFailure(f"Wheel is missing current modules: {', '.join(missing)}")
    if stale_sentinel in record or any(stale_sentinel in member for member in members):
        raise SmokeFailure("Persistent checkout build cache leaked into the wheel.")
    return len(expected_modules)


def smoke(*, offline: bool, wheel_out_dir: Path | None = None) -> dict[str, object]:
    uv = shutil.which("uv")
    if uv is None:
        raise SmokeFailure("uv is required to build and install the wheel.")

    clean_environment = os.environ.copy()
    clean_environment.pop("PYTHONPATH", None)
    clean_environment.pop("VIRTUAL_ENV", None)

    with tempfile.TemporaryDirectory(prefix="yt-insights-wheel-smoke-") as temporary:
        workspace = Path(temporary)
        build_source = _copy_build_source(workspace)
        distribution = workspace / "dist"
        build = _uv_command(uv, "build", offline=offline)
        build.extend(["--wheel", "--out-dir", str(distribution)])
        with _stale_build_sentinel() as stale_sentinel:
            _run(build, cwd=build_source, environment=clean_environment)

        wheels = tuple(distribution.glob("yt_insights-*.whl"))
        if len(wheels) != 1:
            raise SmokeFailure(f"Expected one wheel, found {len(wheels)}.")
        wheel = wheels[0]
        verified_modules = _verify_wheel(
            wheel, build_source, stale_sentinel=stale_sentinel
        )

        corpus = workspace / "corpus" / "channel-a" / "transcripts"
        corpus.mkdir(parents=True)
        shutil.copyfile(
            REPOSITORY_ROOT / "tests" / "fixtures" / "sample.en.vtt",
            corpus / "20260101 - Reliable agents [VideoId_123].en.vtt",
        )
        database = workspace / "search.sqlite3"

        base_venv = workspace / "base-venv"
        base_python = _create_environment(
            uv,
            base_venv,
            str(wheel),
            offline=offline,
            environment=clean_environment,
        )
        base_cli = _script(base_venv, "yt-insights")
        base_mcp = _script(base_venv, "yt-insights-mcp")

        _run([str(base_cli), "--help"], cwd=workspace, environment=clean_environment)
        _run(
            [
                str(base_python),
                "-c",
                (
                    "import importlib.metadata, yt_insights; "
                    f"assert yt_insights.__version__ == '{PACKAGE_VERSION}'; "
                    f"assert importlib.metadata.version('yt-insights') == '{PACKAGE_VERSION}'"
                ),
            ],
            cwd=workspace,
            environment=clean_environment,
        )
        _run(
            [
                str(base_cli),
                "index",
                "--corpus-root",
                str(workspace / "corpus"),
                "--database",
                str(database),
            ],
            cwd=workspace,
            environment=clean_environment,
        )
        search = _run(
            [
                str(base_cli),
                "search",
                "reliable",
                "--database",
                str(database),
                "--limit",
                "1",
                "--json",
            ],
            cwd=workspace,
            environment=clean_environment,
        )
        search_payload = json.loads(search.stdout)
        if len(search_payload.get("hits", [])) != 1:
            raise SmokeFailure("Installed CLI search did not return the fixture passage.")

        missing_mcp = _run(
            [str(base_mcp)],
            cwd=workspace,
            environment=clean_environment,
            expected_code=2,
            input_text="",
        )
        if (
            "MCP support is not installed" not in missing_mcp.stderr
            or "Traceback" in missing_mcp.stderr
        ):
            raise SmokeFailure("Minimal wheel did not report the missing MCP extra cleanly.")

        mcp_venv = workspace / "mcp-venv"
        mcp_python = _create_environment(
            uv,
            mcp_venv,
            f"{wheel}[mcp]",
            offline=offline,
            environment=clean_environment,
        )
        mcp_program = """
import asyncio
from mcp import Client
from yt_insights.mcp_server import create_server

async def check():
    async with Client(create_server(DATABASE)) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == ["search_passages", "get_passage"]
        result = await client.call_tool("search_passages", {"query": "reliable", "limit": 1})
        assert result.is_error is False
        assert result.structured_content["returned"] == 1

asyncio.run(check())
""".replace("DATABASE", repr(str(database)))
        _run(
            [str(mcp_python), "-c", mcp_program],
            cwd=workspace,
            environment=clean_environment,
        )

        stdio_environment = clean_environment | {
            "YT_INSIGHTS_SEARCH_DATABASE": str(database)
        }
        _run(
            [str(_script(mcp_venv, "yt-insights-mcp"))],
            cwd=workspace,
            environment=stdio_environment,
            input_text="",
        )

        saved_wheel: str | None = None
        if wheel_out_dir is not None:
            destination_directory = wheel_out_dir.resolve()
            destination_directory.mkdir(parents=True, exist_ok=True)
            destination = destination_directory / wheel.name
            shutil.copy2(wheel, destination)
            saved_wheel = str(destination)

        result: dict[str, object] = {
            "status": "PASS",
            "wheel": wheel.name,
            "version": PACKAGE_VERSION,
            "minimal_mcp_exit": 2,
            "tools": ["search_passages", "get_passage"],
            "verified_modules": verified_modules,
            "offline": offline,
        }
        if saved_wheel is not None:
            result["saved_wheel"] = saved_wheel
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the minimal wheel and wheel[mcp] outside the checkout."
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Forbid dependency downloads and use only the existing uv cache.",
    )
    parser.add_argument(
        "--wheel-out-dir",
        type=Path,
        help="Copy the verified clean-build wheel to this directory after the smoke.",
    )
    arguments = parser.parse_args()
    try:
        result = smoke(
            offline=arguments.offline,
            wheel_out_dir=arguments.wheel_out_dir,
        )
    except SmokeFailure as error:
        print(f"wheel smoke: FAIL\n{error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
