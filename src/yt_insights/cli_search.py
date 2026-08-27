"""Click adapters for the bounded local transcript-search slice."""

from __future__ import annotations

import json
from pathlib import Path

import click

from .search.corpus import CorpusManifest, scan_corpus
from .search.models import BuildReport, SearchHit, SearchQuery
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
        "excerpt": hit.passage.text,
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
@click.option("--dry-run", is_flag=True, help="Scan and report counts without writing an index.")
@click.option("--status", is_flag=True, help="Validate and report the existing index without scanning.")
def index_command(
    corpus_root: Path,
    database: Path,
    limit: int,
    dry_run: bool,
    status: bool,
) -> None:
    """Build or inspect the deterministic phase-1A search index."""
    if dry_run and status:
        raise click.UsageError("--status and --dry-run cannot be used together.")

    index = SQLiteFtsIndex(database)
    if status:
        try:
            _echo_report(index.status())
        except SearchIndexError as error:
            _raise_index_error(error)
        return

    try:
        manifest = scan_corpus(corpus_root, limit=limit)
    except ValueError as error:
        raise click.ClickException(f"Cannot scan corpus: {error}") from error

    if dry_run:
        _echo_report(_report_from_manifest(manifest))
        return

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
    except SearchIndexNotFound:
        raise click.ClickException("Search index does not exist. Run 'yt-insights index' first.") from None
    except (SearchIndexError, ValueError):
        # Query strings are untrusted input: never echo them in error output.
        raise click.ClickException("Search request is invalid or the index cannot be searched.") from None

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
