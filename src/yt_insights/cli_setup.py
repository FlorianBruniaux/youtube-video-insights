"""CLI commands for optional assistant integration setup."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .assistant_setup import Mode, run_assistant_setup


@click.group("setup")
def setup_group() -> None:
    """Plan, install, or verify optional integrations."""


@setup_group.command("assistants")
@click.option(
    "--client",
    type=click.Choice(("claude", "codex", "both")),
    default="both",
    show_default=True,
)
@click.option(
    "--data-root",
    type=click.Path(path_type=Path),
    help=(
        "Absolute corpus root exposed to the read-only MCP server. "
        "Required unless --assets-only is used."
    ),
)
@click.option(
    "--mcp-command",
    default="yt-insights-mcp",
    show_default=True,
    help="Installed MCP server executable or absolute path.",
)
@click.option("--dry-run", is_flag=True, help="Preview only. This is the default mode.")
@click.option("--apply", is_flag=True, help="Install missing assets and MCP entries.")
@click.option(
    "--verify",
    is_flag=True,
    help="Verify files and client registrations without writes.",
)
@click.option(
    "--assets-only",
    is_flag=True,
    help="Install or verify skills and agents without inspecting MCP registrations.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def assistants_command(
    client: str,
    data_root: Path | None,
    mcp_command: str,
    dry_run: bool,
    apply: bool,
    verify: bool,
    assets_only: bool,
    as_json: bool,
) -> None:
    """Set up portable skills, native researchers, and the read-only MCP."""
    selected = sum((dry_run, apply, verify))
    if selected > 1:
        raise click.UsageError("choose exactly one of --dry-run, --apply, or --verify")
    mode: Mode = "apply" if apply else "verify" if verify else "dry-run"
    try:
        payload, exit_code = run_assistant_setup(
            client=client,
            data_root=data_root,
            mcp_command=mcp_command,
            mode=mode,
            assets_only=assets_only,
        )
    except ValueError as error:
        if as_json:
            click.echo(json.dumps({"status": "invalid", "error": str(error)}, sort_keys=True))
            raise click.exceptions.Exit(2) from None
        raise click.ClickException(str(error)) from error

    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        click.echo(f"Assistant setup: {payload['status']}")
        for operation in payload["operations"]:
            target = operation.get("target", operation.get("client", ""))
            click.echo(f"  {operation['kind']}: {target} [{operation['status']}]")
        conflicts = payload.get("conflicts", [])
        if conflicts:
            click.echo("Conflicts:", err=True)
            for conflict in conflicts:
                click.echo(f"  {conflict}", err=True)
    if exit_code:
        raise click.exceptions.Exit(exit_code)
