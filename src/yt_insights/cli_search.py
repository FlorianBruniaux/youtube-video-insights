"""Click adapters for the bounded local transcript-search slice."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .search.corpus import CorpusManifest, scan_corpus
from .search.models import BuildReport, SearchHit, SearchQuery
from .search.preflight import (
    IndexSpacePreflightError,
    IndexSpacePreflightReport,
    InsufficientIndexSpace,
    preflight_index_space,
)
from .search.service import SearchService
from .search.sqlite_fts import SearchIndexError, SearchIndexNotFound, SQLiteFtsIndex


DEFAULT_CORPUS_ROOT = Path("output")
DEFAULT_DATABASE = DEFAULT_CORPUS_ROOT / ".search" / "search-v1.sqlite3"
MAX_INDEX_LIMIT = 50


def _report_from_manifest(manifest: CorpusManifest) -> BuildReport:
    return BuildReport(
        sources_discovered=manifest.sources_discovered,
        sources_selected=manifest.sources_selected - manifest.sources_invalid,
        sources_invalid=manifest.sources_invalid,
        documents_indexed=len(manifest.documents),
        passages_indexed=len(manifest.passages),
        invalid_sources=tuple(sorted(item.source_relpath for item in manifest.invalid_sources)),
    )


def _echo_report(report: BuildReport) -> None:
    click.echo(f"Sources discovered: {report.sources_discovered}")
    click.echo(f"Sources selected: {report.sources_selected}")
    click.echo(f"Sources invalid: {report.sources_invalid}")
    click.echo(f"Documents: {report.documents_indexed}")
    click.echo(f"Passages: {report.passages_indexed}")


def _echo_preflight(report: IndexSpacePreflightReport) -> None:
    click.echo(f"Preflight candidates discovered: {report.sources_discovered}")
    click.echo(f"Preflight regular files sized: {report.source_files}")
    click.echo(f"Preflight candidates excluded: {report.sources_excluded}")
    click.echo(f"Preflight source bytes: {report.source_bytes}")
    click.echo(f"Preflight required bytes: {report.required_bytes}")
    click.echo(f"Preflight available bytes: {report.available_bytes}")


def _parameter_is_explicit(context: click.Context, name: str) -> bool:
    return context.get_parameter_source(name) is click.core.ParameterSource.COMMANDLINE


def _format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _hit_payload(hit: SearchHit) -> dict[str, int | str]:
    return {
        "rank": hit.rank,
        "channel": hit.document.channel_title,
        "title": hit.document.video_title,
        "language": hit.document.language,
        "excerpt": hit.excerpt,
        "timestamp": _format_timestamp(hit.passage.start_seconds),
        "url": hit.passage.youtube_url,
        "source": hit.document.source_relpath,
    }


def _raise_index_error(error: SearchIndexError) -> None:
    if isinstance(error, SearchIndexNotFound):
        raise click.ClickException("Search index does not exist. Run 'yt-insights index' first.") from error
    raise click.ClickException("Search index is unavailable or invalid. Rebuild it with 'yt-insights index'.") from error


@click.command("index")
@click.option(
    "--corpus-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=DEFAULT_CORPUS_ROOT,
    show_default=True,
    help="Directory containing channel transcript folders.",
)
@click.option(
    "--database",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_DATABASE,
    show_default=True,
    help="Derived SQLite search index path.",
)
@click.option(
    "--limit",
    type=click.IntRange(1, MAX_INDEX_LIMIT),
    default=MAX_INDEX_LIMIT,
    show_default=True,
    help="Maximum transcript files to index in phase 1A.",
)
@click.option(
    "--selection",
    type=click.Choice(("ordered", "representative"), case_sensitive=True),
    default="ordered",
    show_default=True,
    help="Selection strategy for a limited transcript slice.",
)
@click.option(
    "--all",
    "all_sources",
    is_flag=True,
    help="Index every transcript source instead of the default 50-file slice.",
)
@click.option("--dry-run", is_flag=True, help="Scan and report counts without writing an index.")
@click.option("--status", is_flag=True, help="Validate and report the existing index without scanning.")
def index_command(
    corpus_root: Path,
    database: Path,
    limit: int,
    selection: str,
    all_sources: bool,
    dry_run: bool,
    status: bool,
) -> None:
    """Build or inspect the deterministic local transcript search index."""
    context = click.get_current_context()
    explicit_all = _parameter_is_explicit(context, "all_sources")
    explicit_limit = _parameter_is_explicit(context, "limit")
    explicit_selection = _parameter_is_explicit(context, "selection")

    if dry_run and status:
        raise click.UsageError("--status and --dry-run cannot be used together.")
    if all_sources and explicit_limit:
        raise click.UsageError("--all cannot be used with an explicit --limit.")
    if all_sources and explicit_selection:
        raise click.UsageError("--all cannot be used with an explicit --selection.")
    if status and (explicit_all or explicit_limit or explicit_selection):
        raise click.UsageError(
            "--status cannot be used with --all, an explicit --limit, or an explicit --selection."
        )

    index = SQLiteFtsIndex(database)
    if status:
        try:
            _echo_report(index.status())
        except SearchIndexError as error:
            _raise_index_error(error)
        return

    scan_limit = None if all_sources else limit
    scan_selection = "ordered" if all_sources else selection
    preflight_report: IndexSpacePreflightReport | None = None
    if all_sources and not dry_run:
        try:
            preflight_report = preflight_index_space(corpus_root, database)
        except InsufficientIndexSpace as error:
            raise click.ClickException(
                f"Insufficient free space; keep the existing index and free disk space before retrying. {error}"
            ) from error
        except (IndexSpacePreflightError, OSError) as error:
            raise click.ClickException(
                "Cannot verify index capacity. Check corpus and database permissions, then retry."
            ) from error
        except ValueError as error:
            raise click.ClickException(f"Cannot scan corpus: {error}") from error

    try:
        manifest = scan_corpus(corpus_root, limit=scan_limit, selection=scan_selection)
    except ValueError as error:
        raise click.ClickException(f"Cannot scan corpus: {error}") from error

    if dry_run:
        _echo_report(_report_from_manifest(manifest))
        return

    if all_sources and preflight_report is not None:
        _echo_preflight(preflight_report)
    try:
        _echo_report(index.rebuild(manifest))
    except SearchIndexError as error:
        _raise_index_error(error)


@click.command("search")
@click.argument("query")
@click.option(
    "--database",
    type=click.Path(path_type=Path, dir_okay=False),
    default=DEFAULT_DATABASE,
    show_default=True,
    help="SQLite search index path.",
)
@click.option("--channel", default=None, help="Exact channel identifier filter.")
@click.option("--lang", "language", default=None, help="Exact language filter.")
@click.option(
    "--limit",
    type=click.IntRange(1, 20),
    default=10,
    show_default=True,
    help="Maximum ranked results.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit deterministic JSON.")
def search_command(
    query: str,
    database: Path,
    channel: str | None,
    language: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """Search the local transcript index."""
    try:
        request = SearchQuery(query, channel=channel, language=language, limit=limit)
        hits = SearchService(SQLiteFtsIndex(database)).search(request)
    except SearchIndexError as error:
        _raise_index_error(error)
    except ValueError:
        # Query strings are untrusted input: never echo them in error output.
        raise click.ClickException("Search request is invalid.") from None

    payloads = [_hit_payload(hit) for hit in hits]
    if as_json:
        click.echo(json.dumps({"hits": payloads}, ensure_ascii=False, indent=2))
        return
    for payload in payloads:
        click.echo(f"{payload['rank']}. Channel: {payload['channel']}")
        click.echo(f"   Title: {payload['title']}")
        click.echo(f"   Language: {payload['language']}")
        click.echo(f"   Excerpt: {payload['excerpt']}")
        click.echo(f"   Timestamp: {payload['timestamp']}")
        click.echo(f"   URL: {payload['url']}")
        click.echo(f"   Source: {payload['source']}")
