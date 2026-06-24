"""CLI entry point for yt-insights."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .backends import resolve_backend
from .backends.base import BackendNotFoundError, BackendUnavailableError
from .config import CONFIG_TOML_TEMPLATE, load_config


@click.group()
def cli() -> None:
    """Extract structured insights from YouTube transcripts using any LLM."""


@cli.command("list")
@click.argument("source")
def list_cmd(source: str) -> None:
    """List videos in SOURCE without downloading anything."""
    from .downloader import list_videos

    click.echo(f"Fetching video list from {source} ...")
    videos = list_videos(source)
    if not videos:
        click.echo("No videos found.", err=True)
        sys.exit(1)

    click.echo(f"\n  {'#':>3}  {'Date':10}  Title")
    click.echo(f"  {'---':>3}  {'----------':10}  -----")
    for i, v in enumerate(videos, 1):
        click.echo(f"  {i:3}.  {v.formatted_date:10}  {v.title}")
    click.echo(f"\n{len(videos)} video(s) found.")


@cli.command()
@click.argument("source")
@click.option("--skip-download", is_flag=True, help="Skip yt-dlp, use existing VTT files.")
@click.option("--pick", is_flag=True, help="Interactively select which videos to process.")
@click.option("--force", is_flag=True, help="Re-analyze even if insight cache exists.")
@click.option("--model", default=None, help="Override LLM model.")
@click.option("--base-url", default=None, help="Override LLM API base URL.")
@click.option(
    "--concurrency",
    type=int,
    default=None,
    help="Max parallel LLM calls. 0 = auto (default).",
)
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Base directory for yt_transcripts/ and yt_insights/.",
)
@click.option(
    "--sleep-requests",
    type=int,
    default=0,
    help="yt-dlp --sleep-requests value (seconds between requests).",
)
def run(
    source: str,
    skip_download: bool,
    pick: bool,
    force: bool,
    model: str | None,
    base_url: str | None,
    concurrency: int | None,
    output_dir: str | None,
    sleep_requests: int,
) -> None:
    """Download subtitles from SOURCE and extract insights.

    SOURCE can be a YouTube channel URL, playlist URL, video URL,
    or a path to a local file containing one URL per line.
    """
    import questionary

    from .analyzer import analyze_all
    from .downloader import VideoInfo, download_subtitles, list_videos, vtt_to_video_info
    from .reporter import generate_report, load_insights

    # Config
    overrides: dict = {
        "model": model,
        "base_url": base_url,
        "concurrency": concurrency,
    }
    if output_dir:
        base = Path(output_dir)
        overrides["transcripts_dir"] = base / "yt_transcripts"
        overrides["insights_dir"] = base / "yt_insights"
    config = load_config(overrides)

    # Backend (lazy probe)
    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    # Pick mode: interactive video selection
    vtt_files: list[Path] = []
    if pick:
        if skip_download:
            # Select from locally available VTTs
            all_vtts = sorted(config.transcripts_dir.glob("*.vtt"))
            if not all_vtts:
                click.echo(
                    f"No VTT files found in {config.transcripts_dir}/. "
                    "Remove --skip-download to fetch them first.",
                    err=True,
                )
                sys.exit(1)
            videos = [vtt_to_video_info(f) for f in all_vtts]
            vtt_by_id = {vtt_to_video_info(f).video_id: f for f in all_vtts}
        else:
            click.echo(f"Fetching video list from {source} ...")
            videos = list_videos(source)
            if not videos:
                click.echo("No videos found.", err=True)
                sys.exit(1)
            vtt_by_id = None

        choices = [
            questionary.Choice(
                title=f"{v.formatted_date}  {v.title}",
                value=v,
            )
            for v in videos
        ]
        selected: list[VideoInfo] = questionary.checkbox(
            f"Select videos to analyze ({len(videos)} available):",
            choices=choices,
        ).ask()

        if not selected:
            click.echo("No videos selected. Exiting.")
            sys.exit(0)

        click.echo(f"\n{len(selected)} video(s) selected.")

        if not skip_download:
            # Download only selected videos one by one
            for v in selected:
                click.echo(f"  Downloading: {v.title}")
                dl = download_subtitles(
                    v.watch_url, config.transcripts_dir, sleep_requests=sleep_requests
                )
                vtt_files.extend(dl.vtt_files)
                if dl.errors:
                    for e in dl.errors[:2]:
                        click.echo(f"    warning: {e}", err=True)
        else:
            selected_ids = {v.video_id for v in selected}
            vtt_files = [f for vid_id, f in vtt_by_id.items() if vid_id in selected_ids]  # type: ignore[union-attr]

    else:
        # Normal (non-pick) download path
        if not skip_download:
            click.echo(f"Downloading subtitles from {source} ...")
            result = download_subtitles(
                source, config.transcripts_dir, sleep_requests=sleep_requests
            )
            if result.errors:
                for e in result.errors[:5]:
                    click.echo(f"  warning: {e}", err=True)
            vtt_files = result.vtt_files
            click.echo(f"  {len(vtt_files)} subtitle file(s) downloaded.")
        else:
            vtt_files = sorted(config.transcripts_dir.glob("*.vtt"))
            click.echo(f"Found {len(vtt_files)} existing VTT file(s).")

    if not vtt_files:
        click.echo(
            "No VTT files found. Run without --skip-download to fetch subtitles first.",
            err=True,
        )
        sys.exit(1)

    # Analyze
    click.echo(f"\nAnalyzing {len(vtt_files)} video(s) with model '{config.model}' ...")
    try:
        insights = analyze_all(vtt_files, config.insights_dir, backend, config, force=force)
    except BackendUnavailableError as exc:
        click.echo(f"Error: backend unavailable — {exc}", err=True)
        sys.exit(1)
    finally:
        backend.close()

    if not insights:
        click.echo("No usable insights generated.", err=True)
        sys.exit(1)

    click.echo(f"  {len(insights)} insight(s) ready in {config.insights_dir}/")

    # Report
    report_path = config.insights_dir / "AGGREGATE_REPORT.md"
    click.echo(f"\nGenerating aggregate report ...")
    try:
        backend = resolve_backend(config)
        generate_report(insights, backend, config, report_path=report_path)
        backend.close()
    except (BackendNotFoundError, BackendUnavailableError) as exc:
        click.echo(f"Warning: could not generate report — {exc}", err=True)
        return

    click.echo(f"  Report written to {report_path}")
    click.echo("Done.")


@cli.command()
@click.option(
    "--output",
    default=None,
    type=click.Path(),
    help="Path for AGGREGATE_REPORT.md (default: <insights_dir>/AGGREGATE_REPORT.md).",
)
@click.option("--model", default=None)
@click.option("--base-url", default=None)
def report(output: str | None, model: str | None, base_url: str | None) -> None:
    """Generate an aggregate report from existing insight JSON files."""
    from .reporter import generate_report, load_insights

    config = load_config({"model": model, "base_url": base_url})

    insights = load_insights(config.insights_dir)
    if not insights:
        click.echo(
            f"No insight JSON files found in {config.insights_dir}/. "
            "Run 'yt-insights run' first.",
            err=True,
        )
        sys.exit(1)

    report_path = Path(output) if output else config.insights_dir / "AGGREGATE_REPORT.md"

    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    try:
        generate_report(insights, backend, config, report_path=report_path)
    except BackendUnavailableError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        backend.close()

    click.echo(f"Report written to {report_path}")


@cli.group("config")
def config_group() -> None:
    """Manage yt-insights configuration."""


@config_group.command("init")
def config_init() -> None:
    """Create ~/.config/yt-insights/config.toml with commented defaults."""
    config_path = Path.home() / ".config" / "yt-insights" / "config.toml"
    if config_path.exists():
        click.echo(f"Config already exists at {config_path}")
        return
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(CONFIG_TOML_TEMPLATE, encoding="utf-8")
    click.echo(f"Created {config_path}")
