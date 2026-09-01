"""Click lifecycle for the packaged local web interface."""

from __future__ import annotations

import re
import unicodedata
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from http.server import ThreadingHTTPServer
from importlib import resources
from uuid import uuid4

import click

from . import __version__
from .catalog import Catalog, CatalogError
from .config import load_config
from .paths import DataPaths
from .research.acquisition import ResearchAcquisitionService
from .research.assessment import SQLiteEvidenceReader
from .research.discovery import YtDlpDiscoveryProvider
from .research.dossier import (
    DossierExportRequest,
    ensure_dossier_topic_directory,
)
from .research.store import ResearchStore
from .research.workflow import ResearchWorkflow
from .search.service import SearchService
from .search.sqlite_fts import SearchIndexError, SQLiteFtsIndex
from .web.api import SourceAcquisitionFacade
from .web.application import WebApplication
from .web.jobs import JobExecutor
from .web.readers import CatalogWebReader, ExportReader, SearchIndexWebReader
from .web.server import create_server

_LOOPBACK_HOST = "127.0.0.1"
_STARTUP_ERROR = (
    "Local web server is unavailable. "
    "Check the configured databases and port, then retry."
)


@dataclass(frozen=True, slots=True)
class WebRuntime:
    """Resources owned by one local web-server process."""

    server: ThreadingHTTPServer
    jobs: JobExecutor


def _validate_corpus_databases(paths: DataPaths) -> SQLiteFtsIndex:
    index = SQLiteFtsIndex(paths.search_database)
    try:
        index.status()
        with Catalog.open_read_only(paths.catalog_database):
            pass
    except (CatalogError, SearchIndexError, OSError, TypeError, ValueError):
        raise RuntimeError("local corpus databases are unavailable") from None
    return index


def _research_workflow(paths: DataPaths, store: ResearchStore) -> ResearchWorkflow:
    evidence_reader = SQLiteEvidenceReader(
        search_database=paths.search_database,
        catalog_database=paths.catalog_database,
    )

    def existing_video_ids(video_ids: tuple[str, ...]) -> frozenset[str]:
        with Catalog.open_read_only(paths.catalog_database) as catalog:
            return catalog.existing_video_ids(video_ids)

    return ResearchWorkflow(
        store=store,
        evidence_reader=evidence_reader,
        discovery_provider=YtDlpDiscoveryProvider(existing_ids=existing_video_ids),
        acquisition_service=ResearchAcquisitionService(),
        data_paths=paths,
        session_id_factory=lambda: uuid4().hex,
    )


def _topic_slug(topic: str) -> str:
    ascii_topic = unicodedata.normalize("NFKD", topic).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_topic.casefold()).strip("-")
    return slug[:64].rstrip("-") or "research"


def _export_request_factory(
    paths: DataPaths,
    store: ResearchStore,
) -> Callable[[str, bool], DossierExportRequest]:
    def create_request(session_id: str, force: bool) -> DossierExportRequest:
        paths.exports.mkdir(parents=True, exist_ok=True)
        session = store.get_session(session_id)
        prepared = ensure_dossier_topic_directory(
            paths.exports,
            _topic_slug(session.topic),
        )
        created_date = session.created_at.astimezone(UTC).date().isoformat()
        return DossierExportRequest(
            session_id=session_id,
            output_directory=(
                prepared.directory / f"{created_date}-{session.session_id}"
            ),
            force=force,
            root_constraint=prepared.root_constraint,
        )

    return create_request


def create_web_runtime(
    paths: DataPaths,
    *,
    host: str,
    port: int,
) -> WebRuntime:
    """Build validated application dependencies and bind the loopback server."""
    search_index = _validate_corpus_databases(paths)
    store = ResearchStore(paths.research_database)
    workflow = _research_workflow(paths, store)
    jobs = JobExecutor()
    try:
        application = WebApplication(
            search=SearchService(search_index),
            catalog=CatalogWebReader(
                paths.catalog_database,
                search_index=SearchIndexWebReader(search_index),
            ),
            workflow=workflow,
            research_store=store,
            exports=ExportReader(paths.exports),
            jobs=jobs,
            source_acquisition=SourceAcquisitionFacade(paths),
            export_request_factory=_export_request_factory(paths, store),
            package_version=__version__,
        )
        static_root = resources.files("yt_insights.web").joinpath("static")
        server = create_server(
            application,
            host=host,
            port=port,
            static_root=static_root,
        )
    except BaseException:
        jobs.close()
        raise
    return WebRuntime(server=server, jobs=jobs)


@click.command("serve")
@click.option(
    "--port",
    type=click.IntRange(1, 65_535),
    default=8765,
    show_default=True,
)
@click.option("--no-open", is_flag=True, help="Do not open the local browser.")
def serve_command(port: int, no_open: bool) -> None:
    """Serve the local YT Insights interface on loopback."""
    try:
        paths = load_config({}).data_paths
        runtime = create_web_runtime(paths, host=_LOOPBACK_HOST, port=port)
    except Exception:
        raise click.ClickException(_STARTUP_ERROR) from None

    bound_port = int(runtime.server.server_address[1])
    url = f"http://{_LOOPBACK_HOST}:{bound_port}/"
    click.echo(f"Serving YT Insights at {url}")
    try:
        if not no_open:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                click.echo("Warning: the local browser could not be opened.", err=True)
        runtime.server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            runtime.server.server_close()
        finally:
            runtime.jobs.close()
