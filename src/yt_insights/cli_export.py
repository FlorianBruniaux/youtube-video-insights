"""Standalone Click adapter for deterministic transcript exports."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .config import load_config
from .exporter import (
    AmbiguousTranscriptLanguage,
    ExportError,
    VideoExportRequest,
    export_video,
)


@click.group("export")
def export_group() -> None:
    """Export transcript source material without calling an LLM."""


@export_group.command("video")
@click.argument("video_or_url")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("vtt", "txt", "md"), case_sensitive=False),
    default="md",
    show_default=True,
)
@click.option("--lang", "language", help="Exact transcript language to export.")
@click.option(
    "--output",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Output file. Defaults to the configured exports directory.",
)
@click.option(
    "--data-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=None,
    help="Override the configured corpus root for this export.",
)
@click.option("--force", is_flag=True, help="Replace an existing output file.")
@click.option("--json", "json_output", is_flag=True, help="Emit machine-readable result JSON.")
def video_command(
    video_or_url: str,
    output_format: str,
    language: str | None,
    output: Path | None,
    data_root: Path | None,
    force: bool,
    json_output: bool,
) -> None:
    """Export one exact VIDEO_OR_URL transcript source."""
    paths = load_config({"data_root": data_root}).data_paths
    request = VideoExportRequest(
        video_or_url=video_or_url,
        format=output_format,
        language=language,
        output=output,
        force=force,
    )
    try:
        result = export_video(request, paths)
    except AmbiguousTranscriptLanguage as error:
        choices = ", ".join(error.languages)
        raise click.ClickException(
            f"Multiple transcript languages found ({choices}); choose one with --lang."
        ) from error
    except ExportError as error:
        raise click.ClickException(str(error)) from error

    if json_output:
        click.echo(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        click.echo(str(result.path))
