from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tomllib

from click.testing import CliRunner

from yt_insights.cli import cli


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_installed_cli_contract_exposes_agent_facing_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    for command in ("doctor", "acquire", "export", "index", "search"):
        assert command in cli.commands
        assert command in result.output


def test_package_version_matches_project_metadata() -> None:
    from yt_insights import __version__

    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["version"] == "0.2.0"
    assert __version__ == project["project"]["version"]


def test_package_uses_current_spdx_license_metadata() -> None:
    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]

    assert project["license"] == "MIT"
    assert "License :: OSI Approved :: MIT License" not in project["classifiers"]


def test_mcp_console_entrypoint_stays_importable_without_the_optional_extra() -> None:
    script = """
import builtins
import sys

original_import = builtins.__import__

def without_mcp(name, *args, **kwargs):
    if name == 'mcp' or name.startswith('mcp.'):
        error = ModuleNotFoundError("No module named 'mcp'")
        error.name = 'mcp'
        raise error
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_mcp
from yt_insights.mcp_entrypoint import main
main()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "MCP support is not installed" in result.stderr
    assert "uv sync --extra mcp" in result.stderr
    assert "Traceback" not in result.stderr


def test_mcp_console_script_targets_the_lazy_entrypoint() -> None:
    project = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert (
        project["project"]["scripts"]["yt-insights-mcp"]
        == "yt_insights.mcp_entrypoint:main"
    )


def test_readme_does_not_claim_an_unpublished_pypi_install() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pypi.org/project/yt-insights" not in readme
    assert "pipx install yt-insights" not in readme
    assert "pip install yt-insights" not in readme


def test_docs_do_not_present_unwired_mlx_as_an_active_backend() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert "cc-bridge │ Ollama │ Anthropic API │ MLX" not in readme
    assert "1 for Ollama/MLX" not in readme
    assert "any OpenAI-compat, MLX" not in readme


def test_readme_puts_explicit_endpoint_before_automatic_detection() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    backend_section = readme.split("## Backends", 1)[1].split(
        "## CLI reference", 1
    )[0]

    assert "Explicit endpoint" in backend_section
    assert "Priority 0" in backend_section
    assert "short-circuits every automatic probe" in backend_section
    assert backend_section.index("Explicit endpoint") < backend_section.index(
        "Automatic detection order"
    )
    automatic_table = backend_section.split("Automatic detection order", 1)[1]
    assert "Any OpenAI-compatible" not in automatic_table
    assert "2xx or 3xx" in backend_section
    assert "4xx, 429, or 5xx" in backend_section
    assert "below HTTP 500" not in backend_section
    assert "including 4xx responses" not in backend_section


def test_install_guide_forces_ollama_and_describes_probe_contract() -> None:
    install = (REPOSITORY_ROOT / "INSTALL.md").read_text(encoding="utf-8")
    ollama_section = install.split("### Ollama", 1)[1].split("### cc-bridge", 1)[0]
    bridge_section = install.split("### cc-bridge", 1)[1].split(
        "### API Anthropic", 1
    )[0]
    normalized_bridge_section = " ".join(bridge_section.split())

    assert "--base-url http://127.0.0.1:11434/v1" in ollama_section
    assert "`--model` seul" in ollama_section
    assert "réponse 2xx ou 3xx sélectionne cc-bridge" in normalized_bridge_section
    assert "401, 403, 404 ou 429" in normalized_bridge_section
    assert "réponse 5xx" in normalized_bridge_section
    assert "déclenche le repli" in normalized_bridge_section
    assert "toute réponse HTTP inférieure à 500" not in normalized_bridge_section


def test_replayable_wheel_smoke_has_an_offline_mode() -> None:
    script = REPOSITORY_ROOT / "scripts" / "smoke_wheel.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "--offline" in result.stdout
    assert "--wheel-out-dir" in result.stdout
    assert "minimal wheel and wheel[mcp]" in result.stdout


def test_wheel_smoke_copies_only_current_build_inputs(tmp_path: Path) -> None:
    script = REPOSITORY_ROOT / "scripts" / "smoke_wheel.py"
    specification = importlib.util.spec_from_file_location("smoke_wheel", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    copied_source = module._copy_build_source(tmp_path)

    assert (copied_source / "pyproject.toml").is_file()
    assert (copied_source / "README.md").is_file()
    assert (copied_source / "src" / "yt_insights" / "mcp_entrypoint.py").is_file()
    assert not (copied_source / "build").exists()
    assert not tuple((copied_source / "src").glob("*.egg-info"))
    assert not tuple((copied_source / "src").rglob("__pycache__"))


def test_wheel_smoke_rejects_a_working_directory_inside_the_checkout(
    tmp_path: Path,
) -> None:
    script = REPOSITORY_ROOT / "scripts" / "smoke_wheel.py"
    specification = importlib.util.spec_from_file_location("smoke_wheel", script)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)

    outside = tmp_path / "outside"
    outside.mkdir()
    module._require_outside_checkout(outside)

    inside = REPOSITORY_ROOT / "tests"
    try:
        module._require_outside_checkout(inside)
    except module.SmokeFailure as error:
        assert "outside the source checkout" in str(error)
    else:
        raise AssertionError("the smoke accepted a working directory inside the checkout")


def test_install_guide_documents_reproducible_base_and_mcp_setups() -> None:
    install = (REPOSITORY_ROOT / "INSTALL.md").read_text(encoding="utf-8")

    assert "uv sync --extra dev" in install
    assert "uv sync --extra mcp --extra dev" in install
    assert "YT_INSIGHTS_SEARCH_DATABASE" in install
    assert "yt-insights index --all" in install
    assert "yt-insights-mcp" in install
    assert "MLX direct" in install
    assert ".venv/bin/python scripts/smoke_wheel.py --offline" in install
