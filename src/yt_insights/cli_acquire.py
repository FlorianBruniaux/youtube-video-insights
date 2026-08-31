"""Unregistered Click adapter for safe source acquisition."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .acquisition import (
    SourceKind,
    build_acquisition_plan,
    classify_source,
    execute_acquisition,
    read_batch_snapshot,
)
from .config import BACKEND_NAMES, load_config
from .downloader import fetch_video_list


def _years(value: str | None) -> set[int] | None:
    if value is None:
        return None
    try:
        years = {int(item.strip()) for item in value.split(",") if item.strip()}
    except ValueError as exc:
        raise click.BadParameter("years must be comma-separated integers") from exc
    if not years or any(year < 1900 or year > 9999 for year in years):
        raise click.BadParameter("years must be comma-separated integers")
    return years


def _echo_json(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


@click.command("acquire")
@click.argument("source")
@click.option("--slug", default=None, help="Safe corpus slug for a channel or playlist.")
@click.option("--years", default=None, metavar="2025,2026", help="Exact upload years.")
@click.option("--lang", "language", default="fr", show_default=True)
@click.option("--analyze", is_flag=True, help="Generate insights with the selected backend.")
@click.option(
    "--backend",
    type=click.Choice(BACKEND_NAMES),
    default=None,
    help="Select the LLM backend used by --analyze.",
)
@click.option("--dry-run", is_flag=True, help="Discover and print the plan without writes.")
@click.option("--yes", "confirmed", is_flag=True, help="Confirm a multi-video acquisition.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable JSON output.")
@click.option("--data-root", type=click.Path(path_type=Path), default=None)
@click.option("--cookies-from-browser", default=None, metavar="BROWSER")
def acquire(
    source: str,
    slug: str | None,
    years: str | None,
    language: str,
    analyze: bool,
    backend: str | None,
    dry_run: bool,
    confirmed: bool,
    as_json: bool,
    data_root: Path | None,
    cookies_from_browser: str | None,
) -> None:
    """Discover SOURCE, print its plan, then acquire only after confirmation."""
    try:
        source_kind = classify_source(source)
        selected_years = _years(years)
    except (ValueError, click.BadParameter) as exc:
        raise click.BadParameter(str(exc), param_hint="SOURCE" if isinstance(exc, ValueError) else "--years") from exc

    config = load_config({"data_root": data_root, "backend": backend})
    snapshot_urls: tuple[str, ...] = ()
    discovery_sources: tuple[str, ...] = (source,)
    if source_kind is SourceKind.BATCH:
        try:
            snapshot_urls = read_batch_snapshot(Path(source))
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint="SOURCE") from exc
        discovery_sources = snapshot_urls
    discovered: list = []
    discovery_errors: list[str] = []
    for discovery_source in discovery_sources:
        result = fetch_video_list(
            discovery_source, cookies_from_browser=cookies_from_browser
        )
        discovered.extend(result.videos)
        discovery_errors.extend(result.errors)
    try:
        plan = build_acquisition_plan(
            source=source,
            data_paths=config.data_paths,
            slug=slug,
            years=selected_years,
            language=language,
            analyze=analyze,
            discovered=discovered,
            discovery_errors=discovery_errors,
            source_urls=snapshot_urls,
        )
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--slug") from exc

    if dry_run or (plan.requires_confirmation and not confirmed):
        if as_json:
            _echo_json(plan.to_dict())
        else:
            click.echo(
                f"{plan.source_kind.value}: {plan.selected_count} selected -> {plan.output_root}"
            )
            for exclusion in plan.exclusions:
                click.echo(f"excluded: {exclusion}")
        if plan.requires_confirmation and not confirmed and not dry_run:
            raise click.exceptions.Exit(3)
        return

    report = execute_acquisition(
        plan,
        config=config,
        cookies_from_browser=cookies_from_browser,
    )
    if as_json:
        _echo_json(report.to_dict())
    else:
        click.echo(
            f"{report.transcripts_ready}/{report.selected} transcripts ready; "
            f"{report.insights_ready} insights ready"
        )
        for failure in report.failures:
            click.echo(f"failure: {failure}", err=True)
    if report.exit_code:
        raise click.exceptions.Exit(report.exit_code)
