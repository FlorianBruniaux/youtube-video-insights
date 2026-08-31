#!/usr/bin/env python3
"""Build and smoke-test the installable wheel outside the source checkout."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from hashlib import sha256
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
MCP_TOOLS = (
    "list_corpora",
    "search_videos",
    "search_passages",
    "get_passage",
)


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


def _require_outside_checkout(directory: Path) -> None:
    """Fail unless commands will run from a directory outside the checkout."""
    resolved = directory.resolve()
    repository = REPOSITORY_ROOT.resolve()
    if resolved == repository or repository in resolved.parents:
        raise SmokeFailure("Smoke commands must run outside the source checkout.")


def _snapshot_tree(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Return a content-sensitive snapshot suitable for no-write assertions."""
    if not root.exists():
        return ()
    snapshot: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            snapshot.append((relative, "directory", ""))
        elif path.is_file():
            snapshot.append((relative, "file", sha256(path.read_bytes()).hexdigest()))
        else:
            snapshot.append((relative, "other", ""))
    return tuple(snapshot)


def _write_fake_yt_dlp(directory: Path) -> Path:
    """Create a deterministic discovery-only yt-dlp stand-in for dry-run."""
    directory.mkdir(exist_ok=True)
    executable = directory / "yt-dlp"
    executable.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' "
        "'{\"id\":\"nfupYzLjFGc\",\"title\":\"Reliable agents\","
        "\"upload_date\":\"20260101\"}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _write_fail_if_called_client(directory: Path, name: str) -> Path:
    """Create a client stand-in that records any forbidden invocation."""
    directory.mkdir(exist_ok=True)
    executable = directory / name
    executable.write_text(
        "#!/bin/sh\n"
        "if [ -n \"${YT_INSIGHTS_FAKE_CLIENT_LOG:-}\" ]; then\n"
        f"  printf '%s\\n' '{name}' >> \"$YT_INSIGHTS_FAKE_CLIENT_LOG\"\n"
        "fi\n"
        "exit 99\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _verify_cumulative_research_skill(home: Path) -> Path:
    """Require the fourth portable skill after an assets-only install."""
    skill = (
        home
        / ".agents"
        / "skills"
        / "youtube-cumulative-research"
        / "SKILL.md"
    )
    if not skill.is_file():
        raise SmokeFailure("Installed assets omit the cumulative research skill.")
    return skill


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
    expected_assets = {
        source.relative_to(build_source / "src").as_posix()
        for source in (
            build_source / "src" / "yt_insights" / "assistant_assets"
        ).rglob("*")
        if source.is_file()
    }
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
        record_names = [name for name in members if name.endswith(".dist-info/RECORD")]
        if len(record_names) != 1:
            raise SmokeFailure(f"Expected one wheel RECORD, found {len(record_names)}.")
        record = archive.read(record_names[0]).decode("utf-8")

    missing = sorted((expected_modules | expected_assets) - members)
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
        _require_outside_checkout(workspace)
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

        data_root = workspace / "corpus"
        transcripts = data_root / "channel-a" / "transcripts"
        transcripts.mkdir(parents=True)
        transcript = transcripts / "20260101 - Reliable agents [nfupYzLjFGc].en.vtt"
        shutil.copyfile(
            REPOSITORY_ROOT / "tests" / "fixtures" / "sample.en.vtt",
            transcript,
        )
        transcript.with_name(
            "20260101 - Reliable agents [nfupYzLjFGc].info.json"
        ).write_text(
            json.dumps(
                {
                    "id": "nfupYzLjFGc",
                    "title": "Reliable agents",
                    "channel": "Channel A",
                    "channel_id": "channel-a",
                    "upload_date": "20260101",
                }
            ),
            encoding="utf-8",
        )
        search_database = data_root / ".search" / "search-v1.sqlite3"
        catalog_database = data_root / "catalog.sqlite3"

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

        help_result = _run(
            [str(base_cli), "--help"], cwd=workspace, environment=clean_environment
        )
        commands = (
            "doctor",
            "acquire",
            "export",
            "index",
            "research",
            "search",
            "setup",
        )
        for command_name in commands:
            if command_name not in help_result.stdout:
                raise SmokeFailure(
                    f"Installed CLI help is missing the {command_name!r} command."
                )
        research_commands = (
            "start",
            "status",
            "decide",
            "discover",
            "candidates",
            "approve",
            "cancel",
            "acquire",
            "retry",
            "export",
        )
        for command_name in research_commands:
            command_help = _run(
                [str(base_cli), "research", command_name, "--help"],
                cwd=workspace,
                environment=clean_environment,
            )
            argument_marker = "TOPIC" if command_name == "start" else "SESSION_ID"
            if argument_marker not in command_help.stdout:
                raise SmokeFailure(
                    f"Installed research {command_name!r} help omits "
                    f"{argument_marker}."
                )
        research_export_help = _run(
            [str(base_cli), "research", "export", "--help"],
            cwd=workspace,
            environment=clean_environment,
        )
        for marker in ("--output", "--force", "--json"):
            if marker not in research_export_help.stdout:
                raise SmokeFailure(
                    f"Installed research export help omits {marker!r}."
                )
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

        runtime_environment = clean_environment | {
            "YT_INSIGHTS_DATA_ROOT": str(data_root),
            "YT_INSIGHTS_API_KEY": "wheel-smoke-secret-must-not-leak",
            "PATH": (
                f"{base_venv / 'bin'}{os.pathsep}"
                f"{clean_environment.get('PATH', '')}"
            ),
        }
        before_doctor = _snapshot_tree(data_root)
        doctor = _run(
            [str(base_cli), "doctor", "--json"],
            cwd=workspace,
            environment=runtime_environment,
        )
        if "wheel-smoke-secret-must-not-leak" in doctor.stdout + doctor.stderr:
            raise SmokeFailure("doctor --json exposed a configured secret value.")
        if _snapshot_tree(data_root) != before_doctor:
            raise SmokeFailure("doctor --json modified the configured corpus.")

        fake_bin = workspace / "fake-bin"
        _write_fake_yt_dlp(fake_bin)
        _write_fail_if_called_client(fake_bin, "claude")
        _write_fail_if_called_client(fake_bin, "codex")
        fake_client_log = workspace / "assistant-client-invocations.log"
        acquisition_environment = runtime_environment | {
            "PATH": f"{fake_bin}{os.pathsep}{runtime_environment['PATH']}",
        }
        setup_home = workspace / "setup-home"
        setup_environment = acquisition_environment | {
            "HOME": str(setup_home),
            "YT_INSIGHTS_FAKE_CLIENT_LOG": str(fake_client_log),
        }
        setup = _run(
            [
                str(base_cli),
                "setup",
                "assistants",
                "--client",
                "both",
                "--assets-only",
                "--apply",
                "--json",
            ],
            cwd=workspace,
            environment=setup_environment,
        )
        if json.loads(setup.stdout).get("status") != "installed":
            raise SmokeFailure("Installed assistant assets-only setup did not apply.")
        if fake_client_log.exists():
            raise SmokeFailure("Assistant assets-only setup invoked a client process.")
        _verify_cumulative_research_skill(setup_home)

        before_acquire = _snapshot_tree(data_root)
        acquisition = _run(
            [
                str(base_cli),
                "acquire",
                "https://youtu.be/nfupYzLjFGc",
                "--data-root",
                str(data_root),
                "--dry-run",
                "--json",
            ],
            cwd=workspace,
            environment=acquisition_environment,
        )
        acquisition_payload = json.loads(acquisition.stdout)
        if acquisition_payload.get("selected_count") != 1:
            raise SmokeFailure("Installed acquire dry-run did not return one video.")
        if _snapshot_tree(data_root) != before_acquire:
            raise SmokeFailure("Installed acquire --dry-run modified the corpus.")

        exported = workspace / "reliable-agents.md"
        export = _run(
            [
                str(base_cli),
                "export",
                "video",
                "nfupYzLjFGc",
                "--data-root",
                str(data_root),
                "--lang",
                "en",
                "--format",
                "md",
                "--output",
                str(exported),
                "--json",
            ],
            cwd=workspace,
            environment=clean_environment,
        )
        export_payload = json.loads(export.stdout)
        exported_markdown = exported.read_text(encoding="utf-8")
        required_export_markers = (
            "nfupYzLjFGc",
            "https://www.youtube.com/watch?v=nfupYzLjFGc",
            "00:00:00",
        )
        if export_payload.get("format") != "md" or not all(
            marker in exported_markdown for marker in required_export_markers
        ):
            raise SmokeFailure("Installed export omitted source identity or timestamps.")

        _run(
            [
                str(base_cli),
                "index",
                "--corpus-root",
                str(data_root),
                "--database",
                str(search_database),
                "--all",
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
                str(search_database),
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

        _run(
            [
                str(base_cli),
                "catalog",
                "import-corpus",
                str(data_root),
                "--db",
                str(catalog_database),
            ],
            cwd=workspace,
            environment=clean_environment,
        )

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
    async with Client(create_server(SEARCH_DATABASE, CATALOG_DATABASE)) as client:
        tools = await client.list_tools()
        assert [tool.name for tool in tools.tools] == list(MCP_TOOLS)
        corpora = await client.call_tool("list_corpora", {})
        assert corpora.is_error is False
        assert corpora.structured_content["returned"] == 1
        videos = await client.call_tool("search_videos", {"query": "Reliable", "limit": 1})
        assert videos.is_error is False
        assert videos.structured_content["videos"][0]["video_id"] == "nfupYzLjFGc"
        passages = await client.call_tool("search_passages", {"query": "reliable", "limit": 1})
        assert passages.is_error is False
        passage_id = passages.structured_content["hits"][0]["passage_id"]
        passage = await client.call_tool("get_passage", {"passage_id": passage_id})
        assert passage.is_error is False
        assert passage.structured_content["video_id"] == "nfupYzLjFGc"

asyncio.run(check())
""".replace("SEARCH_DATABASE", repr(str(search_database))).replace(
            "CATALOG_DATABASE", repr(str(catalog_database))
        ).replace("MCP_TOOLS", repr(MCP_TOOLS))
        _run(
            [str(mcp_python), "-c", mcp_program],
            cwd=workspace,
            environment=clean_environment,
        )

        stdio_environment = clean_environment | {
            "YT_INSIGHTS_SEARCH_DATABASE": str(search_database),
            "YT_INSIGHTS_CATALOG_DATABASE": str(catalog_database),
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
            "commands": list(commands),
            "research_commands": list(research_commands),
            "cumulative_research_skill": True,
            "tools": list(MCP_TOOLS),
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
