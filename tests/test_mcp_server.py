from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp import Client
import pytest

from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import (
    DocumentRef,
    Passage,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)
from yt_insights.search.sqlite_fts import SQLiteFtsIndex


def _indexed_passage(tmp_path: Path, *, text: str = "safe local retrieval") -> tuple[Path, Passage]:
    document_id = compute_document_id("channel-a", "VideoId_123", "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath="channel-a/transcripts/Search [VideoId_123].en.vtt",
        source_sha256="a" * 64,
        channel_id="channel-a",
        channel_title="Channel A",
        video_id="VideoId_123",
        video_title="Search",
        language="en",
    )
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 12.0, 18.0, text),
        document_id=document_id,
        ordinal=0,
        start_seconds=12.0,
        end_seconds=18.0,
        text=text,
        youtube_url=youtube_url(document.video_id, 12.0),
    )
    database = tmp_path / "search.sqlite3"
    SQLiteFtsIndex(database).rebuild(
        CorpusManifest(
            documents=(document,),
            passages=(passage,),
            invalid_sources=(),
            sources_discovered=1,
            sources_selected=1,
            sources_invalid=0,
        )
    )
    return database, passage


def _large_index(tmp_path: Path) -> Path:
    documents: list[DocumentRef] = []
    passages: list[Passage] = []
    text = " ".join(f"needle {'🧪' * 200}" for _ in range(8))
    for index in range(20):
        video_id = f"Vid{index:08d}"
        document_id = compute_document_id("channel-a", video_id, "en")
        document = DocumentRef(
            document_id=document_id,
            source_relpath=f"channel-a/transcripts/{index}-{'é' * 800}.en.vtt",
            source_sha256=f"{index:064x}",
            channel_id="channel-a",
            channel_title="🧪" * 500,
            video_id=video_id,
            video_title="🧪" * 500,
            language="en",
        )
        passage = Passage(
            passage_id=compute_passage_id(document_id, 0, 12.0, 18.0, text),
            document_id=document_id,
            ordinal=0,
            start_seconds=12.0,
            end_seconds=18.0,
            text=text,
            youtube_url=youtube_url(video_id, 12.0),
        )
        documents.append(document)
        passages.append(passage)
    database = tmp_path / "large.sqlite3"
    SQLiteFtsIndex(database).rebuild(
        CorpusManifest(
            documents=tuple(documents),
            passages=tuple(passages),
            invalid_sources=(),
            sources_discovered=20,
            sources_selected=20,
            sources_invalid=0,
        )
    )
    return database


def _run(coroutine: object) -> object:
    return asyncio.run(coroutine)  # type: ignore[arg-type]


def test_server_exposes_exactly_two_read_only_closed_world_tools(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, _passage = _indexed_passage(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            tools = (await client.list_tools()).tools

        assert [tool.name for tool in tools] == ["search_passages", "get_passage"]
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.open_world_hint is False
            assert "database" not in tool.input_schema.get("properties", {})
            assert "path" not in tool.input_schema.get("properties", {})
            assert "sql" not in tool.input_schema.get("properties", {})
            assert "url" not in tool.input_schema.get("properties", {})
        search_schema = tools[0].input_schema["properties"]
        assert search_schema["query"]["maxLength"] == 500
        assert search_schema["limit"]["minimum"] == 1
        assert search_schema["limit"]["maximum"] == 20
        passage_schema = tools[1].input_schema["properties"]["passage_id"]
        assert passage_schema["pattern"] == "^[0-9a-f]{64}$"

    _run(scenario())


def test_search_passages_uses_the_local_index_and_bounds_its_payload(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, passage = _indexed_passage(tmp_path, text="needle " + "context " * 500)

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            result = await client.call_tool(
                "search_passages",
                {"query": "needle", "channel": "channel-a", "language": "en", "limit": 20},
            )

        assert result.is_error is False
        assert result.structured_content is not None
        (hit,) = result.structured_content["hits"]
        assert hit["passage_id"] == passage.passage_id
        assert hit["url"] == passage.youtube_url
        assert len(hit["excerpt"]) <= 1500
        assert len(json.dumps(result.structured_content, ensure_ascii=False).encode("utf-8")) < 64 * 1024

    _run(scenario())


def test_search_passages_bounds_the_complete_mcp_result_below_64_kib(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database = _large_index(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            result = await client.call_tool(
                "search_passages", {"query": "needle", "limit": 20}
            )

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["truncated"] is True
        assert len(result.model_dump_json(by_alias=True).encode("utf-8")) < 64 * 1024

    _run(scenario())


def test_get_passage_returns_one_bounded_source_backed_record(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, passage = _indexed_passage(
        tmp_path, text="source backed passage " + "🧪" * 5000
    )

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            result = await client.call_tool("get_passage", {"passage_id": passage.passage_id})

        assert result.is_error is False
        assert result.structured_content is not None
        assert result.structured_content["passage_id"] == passage.passage_id
        assert len(result.structured_content["text"]) <= 1500
        assert result.structured_content["text_truncated"] is True
        assert result.structured_content["source"]
        assert len(result.model_dump_json(by_alias=True).encode("utf-8")) < 64 * 1024

    _run(scenario())


def test_invalid_tool_input_and_index_errors_are_clean_mcp_errors(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, _passage = _indexed_passage(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            invalid_query = await client.call_tool("search_passages", {"query": "x" * 501})
            invalid_limit = await client.call_tool(
                "search_passages", {"query": "safe", "limit": 21}
            )
            invalid_id = await client.call_tool("get_passage", {"passage_id": "A" * 64})
        async with Client(create_server(tmp_path / "missing.sqlite3")) as client:
            missing_index = await client.call_tool("search_passages", {"query": "safe"})

        for result in (invalid_query, invalid_limit, invalid_id, missing_index):
            assert result.is_error is True
            assert result.structured_content is None
            rendered = " ".join(getattr(item, "text", "") for item in result.content)
            assert "Traceback" not in rendered
            assert str(database) not in rendered

    _run(scenario())


def test_search_passages_rejects_in_place_corruption_after_mtime_is_restored(
    tmp_path: Path,
) -> None:
    from yt_insights.mcp_server import create_server

    database, _passage = _indexed_passage(tmp_path, text="needle")
    original = database.stat()
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE documents SET source_relpath = '../escape.vtt'")
        connection.commit()
    os.utime(database, ns=(original.st_atime_ns, original.st_mtime_ns))

    async def scenario() -> None:
        async with Client(create_server(database)) as client:
            result = await client.call_tool("search_passages", {"query": "absent"})

        assert result.is_error is True
        assert result.structured_content is None
        rendered = result.model_dump_json(by_alias=True)
        assert "Search index is unavailable" in rendered
        assert '"hits":[]' not in rendered
        assert "escape.vtt" not in rendered

    _run(scenario())


def test_pre_handler_validation_never_reflects_invalid_values_or_extra_fields(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from yt_insights.mcp_server import create_server

    database, _passage = _indexed_passage(tmp_path)
    canaries = {
        "SECRET_QUERY_CANARY",
        "SECRET_CHANNEL_CANARY",
        "SECRET_LANGUAGE_CANARY",
        "SECRET_LIMIT_CANARY",
        "SECRET_PASSAGE_CANARY",
        "SECRET_EXTRA_FIELD",
        "SECRET_EXTRA_VALUE",
    }

    async def scenario() -> list[object]:
        async with Client(create_server(database)) as client:
            return [
                await client.call_tool(
                    "search_passages", {"query": ["SECRET_QUERY_CANARY"]}
                ),
                await client.call_tool(
                    "search_passages",
                    {"query": "safe", "channel": {"value": "SECRET_CHANNEL_CANARY"}},
                ),
                await client.call_tool(
                    "search_passages",
                    {"query": "safe", "language": ["SECRET_LANGUAGE_CANARY"]},
                ),
                await client.call_tool(
                    "search_passages", {"query": "safe", "limit": "SECRET_LIMIT_CANARY"}
                ),
                await client.call_tool(
                    "get_passage", {"passage_id": ["SECRET_PASSAGE_CANARY"]}
                ),
                await client.call_tool(
                    "search_passages",
                    {
                        "query": "safe",
                        "SECRET_EXTRA_FIELD": "SECRET_EXTRA_VALUE",
                    },
                ),
                await client.call_tool(
                    "get_passage",
                    {
                        "passage_id": "f" * 64,
                        "SECRET_EXTRA_FIELD": "SECRET_EXTRA_VALUE",
                    },
                ),
            ]

    results = _run(scenario())
    rendered_results = " ".join(
        result.model_dump_json(by_alias=True) for result in results  # type: ignore[union-attr]
    )
    captured = capsys.readouterr()
    all_output = " ".join((rendered_results, caplog.text, captured.out, captured.err))

    assert all(result.is_error is True for result in results)  # type: ignore[union-attr]
    assert all(result.structured_content is None for result in results)  # type: ignore[union-attr]
    assert all(canary not in all_output for canary in canaries)


def test_get_passage_reports_a_concurrent_database_replacement_as_index_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    database, _passage = _indexed_passage(tmp_path)

    class ReplacingIndex(SQLiteFtsIndex):
        def _require_database_identity(
            self, expected: tuple[int, int, int, int, int]
        ) -> None:
            replacement = database.with_suffix(".replacement")
            replacement.write_bytes(database.read_bytes())
            os.replace(replacement, database)
            super()._require_database_identity(expected)

    monkeypatch.setattr(mcp_server, "SQLiteFtsIndex", ReplacingIndex)

    async def scenario() -> None:
        async with Client(mcp_server.create_server(database)) as client:
            result = await client.call_tool("get_passage", {"passage_id": "f" * 64})

        assert result.is_error is True
        rendered = result.model_dump_json(by_alias=True)
        assert "Search index is unavailable" in rendered
        assert "Passage was not found" not in rendered

    _run(scenario())


def test_main_prefers_an_explicit_database_over_the_environment(
    tmp_path: Path, monkeypatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    explicit = tmp_path / "explicit.sqlite3"
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(tmp_path / "environment.sqlite3"))
    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    monkeypatch.setattr(mcp_server, "create_server", lambda database: captured.update(database=database) or FakeServer())

    mcp_server.main(explicit)

    assert captured == {"database": explicit, "transport": "stdio"}


def test_main_uses_the_environment_then_the_local_default(tmp_path: Path, monkeypatch) -> None:
    import yt_insights.mcp_server as mcp_server

    captured: list[Path] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            assert transport == "stdio"

    monkeypatch.setattr(
        mcp_server,
        "create_server",
        lambda database: captured.append(database) or FakeServer(),
    )
    environment = tmp_path / "environment.sqlite3"
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(environment))
    mcp_server.main()
    monkeypatch.delenv("YT_INSIGHTS_SEARCH_DATABASE")
    mcp_server.main()

    assert captured == [environment, Path("output/.search/search-v1.sqlite3")]
