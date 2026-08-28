"""Standalone Click adapter for read-only runtime diagnostics."""

from __future__ import annotations

import json

import click

from .config import load_config
from .doctor import DoctorReport, inspect_runtime


def _echo_text(report: DoctorReport) -> None:
    click.echo(f"Data root: {report.data_root}")
    for check in report.checks:
        click.echo(f"{check.status.upper():<7} {check.name}: {check.detail}")


@click.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
@click.option(
    "--probe-backends",
    is_flag=True,
    help="Probe only the fixed localhost cc-bridge and Ollama health endpoints.",
)
def doctor_command(as_json: bool, probe_backends: bool) -> None:
    """Inspect the local runtime without mutating the corpus."""
    report = inspect_runtime(load_config({}), probe_backends=probe_backends)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        _echo_text(report)
    if report.has_failures:
        raise click.exceptions.Exit(1)


__all__ = ["doctor_command"]
