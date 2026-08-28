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

from .search.models import DocumentRef, Passage, SearchHit, SearchQuery
from .search.service import SearchService
from .search.sqlite_fts import (
    SearchIndexError,
    SearchPassageNotFound,
    SQLiteFtsIndex,
)


DEFAULT_DATABASE = Path("output/.search/search-v1.sqlite3")
DATABASE_ENVIRONMENT_VARIABLE = "YT_INSIGHTS_SEARCH_DATABASE"
MAX_QUERY_CHARACTERS = 500
MAX_EXCERPT_CHARACTERS = 1500
MAX_RESPONSE_BYTES = 64 * 1024
# MCP structured results are also mirrored as JSON text content. Keeping the
# structured body below 24 KiB leaves room for that copy and protocol metadata.
MAX_STRUCTURED_PAYLOAD_BYTES = 24 * 1024
_PASSAGE_ID_RE = re.compile(r"[0-9a-f]{64}")
_TOOL_NAMES = frozenset(("search_passages", "get_passage"))
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


def create_server(database_path: str | Path) -> MCPServer:
    """Create a closed-world MCP server bound to one local database path."""
    service = SearchService(SQLiteFtsIndex(Path(database_path)))
    server = MCPServer(
        "yt-insights",
        description="Read-only local YouTube transcript passage search.",
        log_level="WARNING",
        middleware=[_SanitizeToolInputMiddleware()],
    )

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


def main(database_path: str | Path | None = None) -> None:
    """Run the local MCP server over stdio."""
    configured = database_path
    if configured is None:
        configured = os.environ.get(DATABASE_ENVIRONMENT_VARIABLE) or DEFAULT_DATABASE
    create_server(Path(configured)).run("stdio")


if __name__ == "__main__":
    main()
