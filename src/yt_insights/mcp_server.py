"""Minimal read-only MCP facade over the local SQLite transcript index."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from .catalog import Catalog, CatalogError, CorpusSummary, VideoSearchResult
from .config import load_config
from .search.models import DocumentRef, Passage, SearchHit, SearchQuery
from .search.service import SearchService
from .search.sqlite_fts import (
    SearchIndexError,
    SearchPassageNotFound,
    SQLiteFtsIndex,
)


# Compatibility import for callers that document the historic layout. main()
# resolves the configured database at execution time instead.
DEFAULT_DATABASE = Path("output/.search/search-v1.sqlite3")
DATABASE_ENVIRONMENT_VARIABLE = "YT_INSIGHTS_SEARCH_DATABASE"
CATALOG_DATABASE_ENVIRONMENT_VARIABLE = "YT_INSIGHTS_CATALOG_DATABASE"
MAX_QUERY_CHARACTERS = 500
MAX_EXCERPT_CHARACTERS = 1500
MAX_RESPONSE_BYTES = 64 * 1024
# MCP structured results are also mirrored as JSON text content. Keeping the
# structured body below 24 KiB leaves room for that copy and protocol metadata.
MAX_STRUCTURED_PAYLOAD_BYTES = 24 * 1024
_PASSAGE_ID_RE = re.compile(r"[0-9a-f]{64}")
_TOOL_NAMES = frozenset(
    ("list_corpora", "search_videos", "search_passages", "get_passage")
)
_LIST_CORPORA_ARGUMENTS = frozenset()
_VIDEO_SEARCH_ARGUMENTS = frozenset(("query", "source", "limit"))
_SEARCH_ARGUMENTS = frozenset(("query", "channel", "language", "limit"))
_PASSAGE_ARGUMENTS = frozenset(("passage_id",))
_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
QueryInput = Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)]
FilterInput = Annotated[str, Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)]
LimitInput = Annotated[int, Field(ge=1, le=20)]
PassageIdInput = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _invalid_tool_result() -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text="Tool input is invalid.")],
        isError=True,
    )


def _valid_bounded_string(value: object, *, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= MAX_QUERY_CHARACTERS
        and "\0" not in value
    )


def _has_invalid_tool_arguments(params: object) -> bool:
    if not isinstance(params, dict):
        return True
    name = params.get("name")
    if not isinstance(name, str) or name not in _TOOL_NAMES:
        return True
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return True
    if name == "list_corpora":
        return set(arguments) != _LIST_CORPORA_ARGUMENTS
    if name == "search_videos":
        if set(arguments) - _VIDEO_SEARCH_ARGUMENTS or "query" not in arguments:
            return True
        limit = arguments.get("limit", 10)
        return (
            not _valid_bounded_string(arguments.get("query"))
            or not _valid_bounded_string(arguments.get("source"), allow_none=True)
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        )
    if name == "search_passages":
        if set(arguments) - _SEARCH_ARGUMENTS or "query" not in arguments:
            return True
        limit = arguments.get("limit", 10)
        return (
            not _valid_bounded_string(arguments.get("query"))
            or not _valid_bounded_string(arguments.get("channel"), allow_none=True)
            or not _valid_bounded_string(arguments.get("language"), allow_none=True)
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 20
        )
    return (
        set(arguments) != _PASSAGE_ARGUMENTS
        or not isinstance((passage_id := arguments.get("passage_id")), str)
        or _PASSAGE_ID_RE.fullmatch(passage_id) is None
    )


class _SanitizeToolInputMiddleware:
    """Reject malformed closed-world tool calls before SDK value reflection."""

    async def __call__(self, context: Any, call_next: Any) -> Any:
        if context.method == "tools/call" and _has_invalid_tool_arguments(context.params):
            return _invalid_tool_result()
        return await call_next(context)


def _clip(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return f"{value[: maximum - 1]}…"


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _search_hit_payload(hit: SearchHit) -> dict[str, object]:
    return {
        "passage_id": hit.passage.passage_id,
        "rank": hit.rank,
        "score": hit.score,
        "channel_id": _clip(hit.document.channel_id, 200),
        "channel": _clip(hit.document.channel_title, 200),
        "title": _clip(hit.document.video_title, 300),
        "language": _clip(hit.document.language, 64),
        "excerpt": _clip(hit.excerpt, MAX_EXCERPT_CHARACTERS),
        "start_seconds": hit.passage.start_seconds,
        "end_seconds": hit.passage.end_seconds,
        "url": hit.passage.youtube_url,
        "source": _clip(hit.document.source_relpath, 500),
    }


def _search_payload(hits: tuple[SearchHit, ...]) -> dict[str, object]:
    payloads = [_search_hit_payload(hit) for hit in hits]
    original_count = len(payloads)
    payload: dict[str, object] = {
        "hits": payloads,
        "returned": original_count,
        "truncated": False,
    }
    while payloads and _serialized_size(payload) >= MAX_STRUCTURED_PAYLOAD_BYTES:
        payloads.pop()
        payload["returned"] = len(payloads)
        payload["truncated"] = True
    if _serialized_size(payload) >= MAX_STRUCTURED_PAYLOAD_BYTES:
        raise ToolError("Search response exceeds the MCP payload limit.")
    return payload


def _passage_payload(document: DocumentRef, passage: Passage) -> dict[str, object]:
    text = _clip(passage.text, MAX_EXCERPT_CHARACTERS)
    payload: dict[str, object] = {
        "passage_id": passage.passage_id,
        "document_id": document.document_id,
        "channel_id": _clip(document.channel_id, 200),
        "channel": _clip(document.channel_title, 200),
        "video_id": document.video_id,
        "title": _clip(document.video_title, 300),
        "language": _clip(document.language, 64),
        "ordinal": passage.ordinal,
        "start_seconds": passage.start_seconds,
        "end_seconds": passage.end_seconds,
        "text": text,
        "text_truncated": text != passage.text,
        "url": passage.youtube_url,
        "source": _clip(document.source_relpath, 500),
        "source_sha256": document.source_sha256,
    }
    if _serialized_size(payload) >= MAX_RESPONSE_BYTES:
        raise ToolError("Passage response exceeds the MCP payload limit.")
    return payload


def _corpus_payload(summary: CorpusSummary) -> dict[str, object]:
    return {
        "source": _clip(summary.source, 200),
        "video_count": summary.video_count,
        "artifact_count": summary.artifact_count,
    }


def _video_payload(result: VideoSearchResult) -> dict[str, object]:
    return {
        "video_id": result.video_id,
        "title": _clip(result.title, 300),
        "published_at": result.published_at,
        "sources": [_clip(source, 200) for source in result.sources],
        "sources_truncated": result.sources_truncated,
        "url": result.watch_url,
        "rank": result.rank,
    }


def _bounded_collection_payload(
    key: str, items: list[dict[str, object]]
) -> dict[str, object]:
    original_count = len(items)
    payload: dict[str, object] = {
        key: items,
        "returned": original_count,
        "truncated": False,
    }
    while items and _serialized_size(payload) >= MAX_STRUCTURED_PAYLOAD_BYTES:
        items.pop()
        payload["returned"] = len(items)
        payload["truncated"] = True
    if _serialized_size(payload) >= MAX_STRUCTURED_PAYLOAD_BYTES:
        raise ToolError("Response exceeds the MCP payload limit.")
    return payload


def _require_absolute_database(database_path: str | Path, *, label: str) -> Path:
    database = Path(database_path).expanduser()
    if not database.is_absolute():
        raise RuntimeError(f"{label} database must be an absolute path.")
    return database


def _validate_search_database(database: Path) -> SQLiteFtsIndex:
    index = SQLiteFtsIndex(database)
    try:
        index.status()
    except SearchIndexError:
        raise RuntimeError(
            "Search database is unavailable or invalid. "
            "Build it with 'yt-insights index --all'."
        ) from None
    return index


def _validate_catalog_database(database: Path) -> None:
    try:
        with Catalog.open_read_only(database):
            pass
    except CatalogError:
        raise RuntimeError(
            "Catalog database is unavailable or invalid. "
            "Build it with 'yt-insights catalog import-corpus'."
        ) from None


def create_server(
    search_database: str | Path,
    catalog_database: str | Path,
) -> MCPServer:
    """Create a closed-world MCP server bound to two validated databases."""
    search_path = _require_absolute_database(search_database, label="Search")
    catalog_path = _require_absolute_database(catalog_database, label="Catalog")
    service = SearchService(_validate_search_database(search_path))
    _validate_catalog_database(catalog_path)
    server = MCPServer(
        "yt-insights",
        description="Read-only local YouTube corpus and passage research.",
        log_level="WARNING",
        middleware=[_SanitizeToolInputMiddleware()],
    )

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS, structured_output=True)
    def list_corpora() -> dict[str, object]:
        """List at most 100 stable corpus summaries without local paths."""
        try:
            with Catalog.open_read_only(catalog_path) as catalog:
                corpora = catalog.list_corpora(limit=100)
            return _bounded_collection_payload(
                "corpora", [_corpus_payload(summary) for summary in corpora]
            )
        except CatalogError:
            raise ToolError("Catalog database is unavailable.") from None

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS, structured_output=True)
    def search_videos(
        query: QueryInput,
        source: FilterInput | None = None,
        limit: LimitInput = 10,
    ) -> dict[str, object]:
        """Search bounded video metadata in the configured local catalog."""
        try:
            with Catalog.open_read_only(catalog_path) as catalog:
                videos = catalog.search_videos(query, source=source, limit=limit)
            return _bounded_collection_payload(
                "videos", [_video_payload(video) for video in videos]
            )
        except ValueError:
            raise ToolError("Video search request is invalid.") from None
        except CatalogError:
            raise ToolError("Catalog database is unavailable.") from None

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS, structured_output=True)
    def search_passages(
        query: QueryInput,
        channel: FilterInput | None = None,
        language: FilterInput | None = None,
        limit: LimitInput = 10,
    ) -> dict[str, object]:
        """Search bounded, source-backed passages in the configured local index."""
        if not isinstance(query, str) or len(query) > MAX_QUERY_CHARACTERS:
            raise ToolError("Search query must contain at most 500 characters.")
        try:
            request = SearchQuery(
                query,
                channel=channel,
                language=language,
                limit=limit,
            )
            return _search_payload(service.search(request))
        except ValueError:
            raise ToolError("Search request is invalid.") from None
        except SearchIndexError:
            raise ToolError(
                "Search index is unavailable. Build it with 'yt-insights index --all'."
            ) from None

    @server.tool(annotations=_READ_ONLY_ANNOTATIONS, structured_output=True)
    def get_passage(passage_id: PassageIdInput) -> dict[str, object]:
        """Return one source-backed passage from the configured local index."""
        if not isinstance(passage_id, str) or _PASSAGE_ID_RE.fullmatch(passage_id) is None:
            raise ToolError("Passage identifier must be a lowercase SHA-256 digest.")
        try:
            document, passage = service.get_passage(passage_id)
            return _passage_payload(document, passage)
        except SearchPassageNotFound:
            raise ToolError("Passage was not found.") from None
        except SearchIndexError:
            raise ToolError(
                "Search index is unavailable. Build it with 'yt-insights index --all'."
            ) from None

    return server


def main(
    search_database: str | Path | None = None,
    catalog_database: str | Path | None = None,
) -> None:
    """Run the local MCP server over stdio."""
    configured_search = search_database
    if configured_search is None:
        configured_search = os.environ.get(DATABASE_ENVIRONMENT_VARIABLE)
    configured_catalog = catalog_database
    if configured_catalog is None:
        configured_catalog = os.environ.get(CATALOG_DATABASE_ENVIRONMENT_VARIABLE)
    if configured_search is None or configured_catalog is None:
        try:
            config = load_config({})
            if configured_search is None:
                configured_search = config.data_paths.search_database
            if configured_catalog is None:
                configured_catalog = config.data_paths.catalog_database
        except Exception:
            raise RuntimeError(
                "yt-insights configuration is unavailable or invalid. "
                "Set both YT_INSIGHTS_SEARCH_DATABASE and "
                "YT_INSIGHTS_CATALOG_DATABASE to absolute database paths."
            ) from None
    create_server(Path(configured_search), Path(configured_catalog)).run("stdio")


if __name__ == "__main__":
    main()
