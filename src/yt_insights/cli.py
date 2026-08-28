"""CLI entry point for yt-insights."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

import click

from .backends import (
    ResolvedBackend,
    format_backend_identity,
    resolve_backend,
    sanitize_endpoint,
)
from .backends.base import BackendNotFoundError, BackendUnavailableError
from .config import BACKEND_NAMES, CONFIG_TOML_TEMPLATE, load_config

SortKey = Literal["date-desc", "date-asc", "title"]

SORT_CHOICES = click.Choice(["date-desc", "date-asc", "title"])
BACKEND_CHOICES = click.Choice(BACKEND_NAMES)


def _echo_resolved_backend(backend: ResolvedBackend) -> None:
    """Display the identity returned by a runtime backend resolution."""
    click.echo(f"Resolved backend: {format_backend_identity(backend.identity)}")


def _sort_videos(videos: list, sort: SortKey) -> list:
    if sort == "date-desc":
        return sorted(videos, key=lambda v: v.upload_date or "", reverse=True)
    if sort == "date-asc":
        return sorted(videos, key=lambda v: v.upload_date or "")
    return sorted(videos, key=lambda v: v.title.lower())


@click.group()
def cli() -> None:
    """YouTube transcript analysis and Shorts suggestion tool.

    Two pipelines, with explicit backend selection or automatic detection:

    \b
    Insight pipeline:
      yt-insights run SOURCE          Download subtitles + extract per-video insights
      yt-insights report              Regenerate aggregate report from cached insights

    \b
    Shorts pipeline:
      yt-insights suggest-shorts      Identify top 3 Short moments per talk (30-90s)
      yt-insights generate-short ID   Download a single clip segment via yt-dlp

    \b
    Discovery:
      yt-insights list SOURCE         List videos without downloading anything
      yt-insights config show         Print effective configuration (no runtime backend probe)
      yt-insights config init         Create ~/.config/yt-insights/config.toml
    """


@cli.command("list")
@click.argument("source")
@click.option(
    "--sort",
    type=SORT_CHOICES,
    default="date-desc",
    show_default=True,
    help="Sort order.",
)
@click.option(
    "--cookies-from-browser",
    default=None,
    metavar="BROWSER",
    help="Read cookies from BROWSER (chrome, firefox, safari...) to avoid rate-limiting.",
)
def list_cmd(source: str, sort: SortKey, cookies_from_browser: str | None) -> None:
    """List videos in SOURCE without downloading anything."""
    from .downloader import list_videos

    click.echo(f"Fetching video list from {source} ...")
    videos = _sort_videos(
        list_videos(source, cookies_from_browser=cookies_from_browser), sort
    )
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
@click.option("--pick", is_flag=True, help="Interactively search and select which videos to process.")
@click.option(
    "--sort",
    type=SORT_CHOICES,
    default="date-desc",
    show_default=True,
    help="Sort order for --pick mode.",
)
@click.option("--force", is_flag=True, help="Re-analyze even if insight cache exists.")
@click.option("--model", default=None, help="Override LLM model.")
@click.option("--base-url", default=None, help="Override LLM API base URL.")
@click.option("--backend", type=BACKEND_CHOICES, default=None, help="Select the LLM backend.")
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
    help="Base output directory (default: output/).",
)
@click.option(
    "--sleep-requests",
    type=int,
    default=0,
    help="Seconds to wait between yt-dlp requests (rate limiting). Try 1-3 if blocked.",
)
@click.option(
    "--cookies-from-browser",
    default=None,
    metavar="BROWSER",
    help="Read cookies from BROWSER (chrome, firefox, safari...) to avoid rate-limiting.",
)
def run(
    source: str,
    skip_download: bool,
    pick: bool,
    sort: SortKey,
    force: bool,
    model: str | None,
    base_url: str | None,
    backend: str | None,
    concurrency: int | None,
    output_dir: str | None,
    sleep_requests: int,
    cookies_from_browser: str | None,
) -> None:
    """Download subtitles from SOURCE and extract insights.

    SOURCE can be a YouTube channel URL, playlist URL, video URL,
    or a path to a local file containing one URL per line.
    """
    from .analyzer import analyze_all
    from .downloader import VideoInfo, download_subtitles, list_videos, vtt_to_video_info
    from .reporter import generate_report, load_insights

    # Config
    overrides: dict = {
        "model": model,
        "base_url": base_url,
        "backend": backend,
        "concurrency": concurrency,
    }
    if output_dir:
        base = Path(output_dir)
        overrides["transcripts_dir"] = base / "transcripts"
        overrides["insights_dir"] = base / "insights"
    config = load_config(overrides)

    # Backend (lazy probe)
    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    _echo_resolved_backend(backend)

    # Pick mode: fuzzy interactive video selection
    vtt_files: list[Path] = []
    if pick:
        from InquirerPy import inquirer

        if skip_download:
            all_vtts = sorted(config.transcripts_dir.glob("*.vtt"))
            if not all_vtts:
                click.echo(
                    f"No VTT files found in {config.transcripts_dir}/. "
                    "Remove --skip-download to fetch them first.",
                    err=True,
                )
                sys.exit(1)
            videos = _sort_videos([vtt_to_video_info(f) for f in all_vtts], sort)
            vtt_by_id = {vtt_to_video_info(f).video_id: f for f in all_vtts}
        else:
            click.echo(f"Fetching video list from {source} ...")
            videos = _sort_videos(
                list_videos(source, cookies_from_browser=cookies_from_browser), sort
            )
            if not videos:
                click.echo("No videos found.", err=True)
                sys.exit(1)
            vtt_by_id = None

        # Use display strings as choices; look up VideoInfo after selection.
        # Avoids InquirerPy Choice.value inconsistencies across versions.
        choice_labels = [f"{v.formatted_date}  {v.title}" for v in videos]
        label_to_video = {label: v for label, v in zip(choice_labels, videos)}

        selected_labels: list[str] = inquirer.fuzzy(
            message=f"Search and select videos ({len(videos)} available, Tab to toggle):",
            choices=choice_labels,
            multiselect=True,
            max_height="70%",
        ).execute() or []
        selected: list[VideoInfo] = [label_to_video[lbl] for lbl in selected_labels if lbl in label_to_video]

        if not selected:
            click.echo("No videos selected. Exiting.")
            sys.exit(0)

        click.echo(f"\n{len(selected)} video(s) selected.")

        if not skip_download:
            for v in selected:
                click.echo(f"  Downloading: {v.title}")
                dl = download_subtitles(
                    v.watch_url,
                    config.transcripts_dir,
                    sleep_requests=sleep_requests,
                    cookies_from_browser=cookies_from_browser,
                    sub_langs=config.sub_langs,
                )
                if dl.vtt_files:
                    vtt_files.extend(dl.vtt_files)
                else:
                    # yt-dlp may not log paths if the file was already present
                    # and skipped without a "already exists" message.
                    # Fall back to scanning by video_id.
                    existing = [
                        f for f in config.transcripts_dir.glob("*.vtt")
                        if v.video_id in f.name
                    ]
                    if existing:
                        click.echo(f"    (using cached VTT)")
                        vtt_files.extend(existing)
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
                source,
                config.transcripts_dir,
                sleep_requests=sleep_requests,
                cookies_from_browser=cookies_from_browser,
                sub_langs=config.sub_langs,
            )
            if result.errors:
                for e in result.errors[:5]:
                    click.echo(f"  warning: {e}", err=True)
            vtt_files = result.vtt_files
            click.echo(f"  {len(vtt_files)} subtitle file(s) downloaded.")
        else:
            # If source looks like a single video URL, filter by its video ID
            import re as _re
            _vid_match = _re.search(r"[?&]v=([A-Za-z0-9_-]{11})", source)
            if _vid_match:
                vid_id = _vid_match.group(1)
                vtt_files = [f for f in config.transcripts_dir.glob("*.vtt") if vid_id in f.name]
                if not vtt_files:
                    click.echo(
                        f"No cached VTT for video {vid_id} in {config.transcripts_dir}/. "
                        "Remove --skip-download to fetch it.",
                        err=True,
                    )
                    sys.exit(1)
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
    click.echo(
        f"\nAnalyzing {len(vtt_files)} video(s) with model "
        f"'{backend.identity.model}' ..."
    )
    try:
        insights = analyze_all(
            vtt_files,
            config.insights_dir,
            backend,
            config,
            force=force,
            on_transcript_usage=lambda _path, usage: click.echo(usage.format_message()),
        )
    except BackendUnavailableError as exc:
        click.echo(f"Error: backend unavailable — {exc}", err=True)
        sys.exit(1)
    finally:
        backend.close()

    if not insights:
        click.echo("No usable insights generated.", err=True)
        sys.exit(1)

    click.echo(f"  {len(insights)} insight(s) generated:")
    for vi in insights:
        md_path = vi.insight_path.with_suffix(".md")
        if md_path.exists():
            click.echo(f"    {md_path}")

    # Report
    report_path = config.insights_dir / "AGGREGATE_REPORT.md"
    click.echo("\nGenerating aggregate report ...")
    report_backend: ResolvedBackend | None = None
    try:
        report_backend = resolve_backend(config)
        _echo_resolved_backend(report_backend)
        generate_report(insights, report_backend, config, report_path=report_path)
    except (BackendNotFoundError, BackendUnavailableError) as exc:
        click.echo(f"Warning: could not generate report — {exc}", err=True)
        return
    finally:
        if report_backend is not None:
            report_backend.close()

    click.echo(f"  Aggregate  → {report_path}")
    full_report_path = report_path.parent / "FULL_REPORT.md"
    if full_report_path.exists():
        click.echo(f"  Full       → {full_report_path}")
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
@click.option("--backend", type=BACKEND_CHOICES, default=None)
def report(
    output: str | None,
    model: str | None,
    base_url: str | None,
    backend: str | None,
) -> None:
    """Generate an aggregate report from existing insight JSON files."""
    from .reporter import generate_report, load_insights

    config = load_config(
        {"model": model, "base_url": base_url, "backend": backend}
    )

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
    _echo_resolved_backend(backend)

    try:
        generate_report(insights, backend, config, report_path=report_path)
    except BackendUnavailableError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        backend.close()

    click.echo(f"Report written to {report_path}")


@cli.command("suggest-shorts")
@click.option("--vtt", "vtt_path", default=None, type=click.Path(exists=True), help="Single VTT file to process.")
@click.option("--force", is_flag=True, help="Re-analyze even if suggestion cache exists.")
@click.option("--index-only", is_flag=True, help="Regenerate only INDEX.md from existing caches.")
@click.option("--model", default=None, help="Override LLM model.")
@click.option("--base-url", default=None, help="Override LLM API base URL.")
@click.option("--backend", type=BACKEND_CHOICES, default=None, help="Select the LLM backend.")
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Base output directory (default: output/).",
)
def suggest_shorts_cmd(
    vtt_path: str | None,
    force: bool,
    index_only: bool,
    model: str | None,
    base_url: str | None,
    backend: str | None,
    output_dir: str | None,
) -> None:
    """Identify the top 3 Short-worthy moments in each VTT transcript.

    Reads from <transcripts_dir>/*.vtt and writes suggestions to <shorts_dir>/.
    Skips already-processed talks unless --force is set.
    """
    from .shorts import generate_index, suggest_all, suggest_shorts

    overrides: dict = {"model": model, "base_url": base_url, "backend": backend}
    if output_dir:
        base = Path(output_dir)
        overrides["transcripts_dir"] = base / "transcripts"
        overrides["insights_dir"] = base / "insights"
        overrides["shorts_dir"] = base / "shorts"
    config = load_config(overrides)

    config.shorts_dir.mkdir(parents=True, exist_ok=True)

    if index_only:
        index_md = generate_index(config.shorts_dir)
        index_path = config.shorts_dir / "INDEX.md"
        index_path.write_text(index_md, encoding="utf-8")
        click.echo(f"Index regenerated: {index_path}")
        return

    try:
        backend = resolve_backend(config)
    except BackendNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    _echo_resolved_backend(backend)

    if vtt_path:
        vtt_files = [Path(vtt_path)]
    else:
        vtt_files = sorted(config.transcripts_dir.glob("*.vtt"))
        if not vtt_files:
            click.echo(
                f"No VTT files found in {config.transcripts_dir}/. "
                "Run 'yt-insights run' first.",
                err=True,
            )
            sys.exit(1)

    cached = sum(1 for f in vtt_files if (config.shorts_dir / f"{f.stem}.json").exists())
    to_process = len(vtt_files) - cached if not force else len(vtt_files)
    click.echo(
        f"\n{len(vtt_files)} VTT file(s) found — "
        f"{cached} cached, {to_process} to process — "
        f"model '{backend.identity.model}'"
    )

    try:
        results = suggest_all(
            vtt_files,
            config.insights_dir,
            config.shorts_dir,
            backend,
            config,
            force=force,
            on_transcript_usage=lambda _path, usage: click.echo(usage.format_message()),
        )
    except BackendUnavailableError as exc:
        click.echo(f"Error: backend unavailable — {exc}", err=True)
        sys.exit(1)
    finally:
        backend.close()

    total_suggestions = sum(len(r.suggestions) for r in results)
    click.echo(f"\n{len(results)} talk(s) processed, {total_suggestions} suggestion(s) generated.")

    click.echo("Generating global index ...")
    index_md = generate_index(config.shorts_dir)
    index_path = config.shorts_dir / "INDEX.md"
    index_path.write_text(index_md, encoding="utf-8")
    click.echo(f"  Index -> {index_path}")
    click.echo("Done.")


@cli.command("generate-short")
@click.argument("video_id")
@click.option("--start", required=True, help="Start timestamp HH:MM:SS.")
@click.option("--end", required=True, help="End timestamp HH:MM:SS.")
@click.option("--title", default="", help="Short title for output filename.")
@click.option(
    "--output-dir",
    type=click.Path(),
    default=None,
    help="Directory for clip output (default: output/clips/).",
)
@click.option(
    "--output-format",
    default="mp4",
    show_default=True,
    help="Container format for the clip (mp4, webm, mkv). Requires ffmpeg for mp4/mkv.",
)
def generate_short_cmd(
    video_id: str,
    start: str,
    end: str,
    title: str,
    output_dir: str | None,
    output_format: str,
) -> None:
    """Download a single Short clip from YouTube using yt-dlp.

    VIDEO_ID is the 11-character YouTube video ID (e.g. rAfAnJcuymo).
    Only the specified segment is downloaded (no full-video fetch).

    Example:
      yt-insights generate-short rAfAnJcuymo --start 00:05:10 --end 00:05:55 --title "hook-claude"
    """
    from .shorts import generate_short_clip

    config = load_config({})
    clips_dir = Path(output_dir) if output_dir else config.shorts_clips_dir

    click.echo(f"Downloading clip {video_id} [{start} -> {end}] ...")
    clip_path = generate_short_clip(video_id, start, end, clips_dir, title=title, output_format=output_format)
    if clip_path:
        click.echo(f"  Saved: {clip_path}")
    else:
        click.echo("  Clip download failed. Check yt-dlp is installed and the video is accessible.", err=True)
        sys.exit(1)


@cli.command("interactive")
@click.option("--action",   default=None, type=click.Choice(["insights", "shorts", "clip", "pipeline"]), help="Action à exécuter.")
@click.option("--source",   default=None, help="URL YouTube (vidéo, playlist ou chaîne).")
@click.option("--duration", default=None, type=click.Choice(["very-short", "standard", "long", "any"]), help="Durée préférée du Short.")
@click.option("--platform", default=None, type=click.Choice(["youtube-shorts", "tiktok", "reels", "linkedin", "none"]), help="Plateforme cible.")
@click.option("--format",   "output_format", default=None, type=click.Choice(["mp4", "webm", "mkv"]), help="Format de sortie du clip.")
@click.option("--backend", type=BACKEND_CHOICES, default=None, help="Route LLM explicite.")
def interactive_cmd(
    action: str | None,
    source: str | None,
    duration: str | None,
    platform: str | None,
    output_format: str | None,
    backend: str | None,
) -> None:
    """Wizard interactif. En TTY : InquirerPy. Sans TTY : passe les flags directement.

    \b
    Sans TTY (ex. Claude Code), fournir tous les flags :
      yt-insights interactive --action pipeline --source URL \\
        --duration standard --platform youtube-shorts --format mp4
    """
    from .wizard import run_wizard
    run_wizard(
        action=action,
        source=source,
        duration=duration,
        platform=platform,
        output_format=output_format,
        backend=backend,
    )


_DEFAULT_CATALOG_DB = "output/catalog.sqlite3"


def _echo_catalog_run(summary: object) -> None:
    click.echo(
        f"run={summary.run_id} status={summary.status} "
        f"seen={summary.items_seen} written={summary.items_written} "
        f"errors={summary.error_count}"
    )


@cli.group("catalog")
def catalog_group() -> None:
    """Build and query the local SQLite video catalog."""


@catalog_group.command("import-corpus")
@click.argument(
    "corpus_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG_DB,
    show_default=True,
)
def catalog_import_corpus(corpus_root: Path, db_path: Path) -> None:
    """Import an existing output corpus without modifying its files."""
    from .catalog import Catalog

    try:
        with Catalog(db_path) as catalog:
            summary = catalog.import_corpus(corpus_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_catalog_run(summary)


@catalog_group.command("discover")
@click.argument("source")
@click.option(
    "--cookies-from-browser",
    default=None,
    metavar="BROWSER",
    help="Read cookies from BROWSER when the source requires them.",
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG_DB,
    show_default=True,
)
def catalog_discover(
    source: str,
    cookies_from_browser: str | None,
    db_path: Path,
) -> None:
    """Discover a source through yt-dlp and persist its result and errors."""
    from .catalog import Catalog
    from .downloader import fetch_video_list

    result = fetch_video_list(
        source,
        cookies_from_browser=cookies_from_browser,
    )
    try:
        with Catalog(db_path) as catalog:
            summary = catalog.ingest_discovery(source, result)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_catalog_run(summary)


@catalog_group.command("search")
@click.argument("query")
@click.option("--source", default=None, help="Restrict results to one source slug.")
@click.option(
    "--limit",
    type=click.IntRange(1, 100),
    default=20,
    show_default=True,
)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG_DB,
    show_default=True,
)
def catalog_search(
    query: str,
    source: str | None,
    limit: int,
    db_path: Path,
) -> None:
    """Search titles, sources, insight text, and cleaned transcripts."""
    from .catalog import Catalog

    if not query.strip():
        raise click.UsageError("Search query cannot be empty")
    try:
        with Catalog(db_path) as catalog:
            results = catalog.search(query, source=source, limit=limit)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if not results:
        click.echo("No videos found.")
        return
    for result in results:
        date = result.published_at or "unknown-date"
        sources = ",".join(result.sources) or "unknown-source"
        click.echo(f"{date}  {sources}  {result.title}  [{result.video_id}]")
        click.echo(f"  {result.watch_url}")
        if result.highlight:
            click.echo(f"  {result.highlight}")


@catalog_group.command("stats")
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG_DB,
    show_default=True,
)
def catalog_stats(db_path: Path) -> None:
    """Print stable domain counts for the local catalog."""
    from .catalog import Catalog

    try:
        with Catalog(db_path) as catalog:
            stats = catalog.stats()
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"videos={stats.videos} sources={stats.sources} "
        f"artifacts={stats.artifacts} runs={stats.runs} errors={stats.errors}"
    )


@catalog_group.command("errors")
@click.option("--run-id", type=click.IntRange(1), default=None)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=_DEFAULT_CATALOG_DB,
    show_default=True,
)
def catalog_errors(run_id: int | None, db_path: Path) -> None:
    """List durable collection/import errors, optionally for one run."""
    from .catalog import Catalog

    try:
        with Catalog(db_path) as catalog:
            errors = catalog.list_errors(run_id=run_id)
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if not errors:
        click.echo("No collection errors.")
        return
    for error in errors:
        item = f" item={error.item_ref}" if error.item_ref else ""
        click.echo(
            f"run={error.run_id} stage={error.stage} source={error.source}"
            f"{item} error={error.message}"
        )


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


@config_group.command("show")
@click.option("--model", default=None, help="Simulate --model override.")
@click.option("--base-url", default=None, help="Simulate --base-url override.")
@click.option("--backend", type=BACKEND_CHOICES, default=None, help="Simulate --backend override.")
def config_show(
    model: str | None, base_url: str | None, backend: str | None
) -> None:
    """Print effective configuration values and their sources without probing.

    Merges defaults → config.toml → env vars → CLI flags so you can verify
    what yt-insights will actually use when you run a command.
    """
    import os as _os

    config_path = Path.home() / ".config" / "yt-insights" / "config.toml"
    config = load_config(
        {"model": model, "base_url": base_url, "backend": backend}
    )

    def _src(env_key: str, flag_val: object, default: object) -> str:
        if flag_val is not None:
            return "CLI flag"
        if _os.getenv(env_key):
            return f"env {env_key}"
        if config_path.exists():
            return "config.toml (or default)"
        return "default"

    click.echo("\nyt-insights effective configuration (no runtime backend probe)")
    click.echo(f"Config file : {config_path} ({'exists' if config_path.exists() else 'not found, using defaults'})")
    click.echo()

    rows = [
        ("backend",              config.backend,              "YT_INSIGHTS_BACKEND",      backend),
        ("base_url",             sanitize_endpoint(config.base_url), "YT_INSIGHTS_BASE_URL",      base_url),
        ("model",                config.model,                "YT_INSIGHTS_MODEL",                model),
        ("api_key",              "***" if config.api_key else "(not set)", "YT_INSIGHTS_API_KEY", None),
        ("max_transcript_chars", config.max_transcript_chars, "YT_INSIGHTS_MAX_TRANSCRIPT_CHARS",  None),
        ("max_tokens",           config.max_tokens,           "YT_INSIGHTS_MAX_TOKENS",            None),
        ("timeout",              config.timeout,              "YT_INSIGHTS_TIMEOUT",               None),
        ("concurrency",          f"{config.concurrency} (0 = auto)", "YT_INSIGHTS_CONCURRENCY",   None),
        ("transcripts_dir",      config.transcripts_dir,      "YT_INSIGHTS_TRANSCRIPTS_DIR",       None),
        ("insights_dir",         config.insights_dir,         "YT_INSIGHTS_INSIGHTS_DIR",          None),
        ("shorts_dir",           config.shorts_dir,           "YT_INSIGHTS_SHORTS_DIR",            None),
        ("shorts_clips_dir",     config.shorts_clips_dir,     "YT_INSIGHTS_SHORTS_CLIPS_DIR",      None),
    ]

    width = max(len(k) for k, *_ in rows)
    for key, value, env_key, flag_val in rows:
        src = _src(env_key, flag_val, None)
        click.echo(f"  {key:<{width}}  {str(value):<40}  # {src}")


from .cli_acquire import acquire
from .cli_doctor import doctor_command
from .cli_export import export_group
from .cli_search import index_command, search_command


cli.add_command(acquire)
cli.add_command(doctor_command)
cli.add_command(export_group)
cli.add_command(index_command)
cli.add_command(search_command)
