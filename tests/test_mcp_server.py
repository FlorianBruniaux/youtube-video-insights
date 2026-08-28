from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp import Client
import pytest

from yt_insights.catalog import Catalog
from yt_insights.downloader import VideoInfo, VideoListResult
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


def _catalog_database(tmp_path: Path) -> Path:
    database = tmp_path / "catalog.sqlite3"
    with Catalog(database) as catalog:
        catalog.checkpoint()
    return database


def _server(search_database: Path, tmp_path: Path):
    from yt_insights.mcp_server import create_server

    return create_server(search_database, _catalog_database(tmp_path))


def test_server_exposes_exactly_four_read_only_closed_world_tools(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, _passage = _indexed_passage(tmp_path)
    catalog_database = _catalog_database(tmp_path)

    async def scenario() -> None:
        async with Client(create_server(database, catalog_database)) as client:
            tools = (await client.list_tools()).tools

        assert [tool.name for tool in tools] == [
            "list_corpora",
            "search_videos",
            "search_passages",
            "get_passage",
        ]
        for tool in tools:
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint is True
            assert tool.annotations.open_world_hint is False
            assert "database" not in tool.input_schema.get("properties", {})
            assert "path" not in tool.input_schema.get("properties", {})
            assert "sql" not in tool.input_schema.get("properties", {})
            assert "url" not in tool.input_schema.get("properties", {})
        assert tools[0].input_schema.get("properties", {}) == {}
        video_schema = tools[1].input_schema["properties"]
        assert set(video_schema) == {"query", "source", "limit"}
        assert video_schema["limit"]["minimum"] == 1
        assert video_schema["limit"]["maximum"] == 20
        search_schema = tools[2].input_schema["properties"]
        assert search_schema["query"]["maxLength"] == 500
        assert search_schema["limit"]["minimum"] == 1
        assert search_schema["limit"]["maximum"] == 20
        passage_schema = tools[3].input_schema["properties"]["passage_id"]
        assert passage_schema["pattern"] == "^[0-9a-f]{64}$"

    _run(scenario())


def test_discovery_tools_return_bounded_metadata_without_paths_or_transcripts(
    tmp_path: Path,
) -> None:
    from yt_insights.mcp_server import create_server

    search_database, _passage = _indexed_passage(tmp_path)
    catalog_database = tmp_path / "catalog.sqlite3"
    with Catalog(catalog_database) as catalog:
        for index in range(105):
            catalog.ingest_discovery(
                f"https://www.youtube.com/@source-{index:03d}/videos",
                VideoListResult(
                    videos=[
                        VideoInfo(
                            video_id=f"V{index:010d}",
                            title=f"Metadata needle {index:03d}",
                            upload_date="20260828",
                        )
                    ],
                    errors=[],
                    returncode=0,
                ),
            )
        catalog.checkpoint()

    before_search_database = search_database.read_bytes()
    before_database = catalog_database.read_bytes()
    before_sidecars = sorted(path.name for path in tmp_path.iterdir())

    async def scenario() -> None:
        async with Client(create_server(search_database, catalog_database)) as client:
            corpora = await client.call_tool("list_corpora", {})
            found = await client.call_tool(
                "search_videos",
                {"query": "metadata needle", "source": "source-000", "limit": 20},
            )

        assert corpora.is_error is False
        assert found.is_error is False
        assert corpora.structured_content is not None
        assert found.structured_content is not None
        assert len(corpora.structured_content["corpora"]) == 100
        assert len(found.structured_content["videos"]) <= 20
        rendered = json.dumps(
            [corpora.structured_content, found.structured_content], ensure_ascii=False
        )
        assert str(tmp_path) not in rendered
        assert "SELECT " not in rendered
        assert "Traceback" not in rendered
        assert "transcript" not in rendered.lower()

    _run(scenario())
    assert search_database.read_bytes() == before_search_database
    assert catalog_database.read_bytes() == before_database
    assert sorted(path.name for path in tmp_path.iterdir()) == before_sidecars


def test_search_passages_uses_the_local_index_and_bounds_its_payload(tmp_path: Path) -> None:
    from yt_insights.mcp_server import create_server

    database, passage = _indexed_passage(tmp_path, text="needle " + "context " * 500)

    async def scenario() -> None:
        async with Client(_server(database, tmp_path)) as client:
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
        async with Client(_server(database, tmp_path)) as client:
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
        async with Client(_server(database, tmp_path)) as client:
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
        async with Client(_server(database, tmp_path)) as client:
            invalid_query = await client.call_tool("search_passages", {"query": "x" * 501})
            invalid_limit = await client.call_tool(
                "search_passages", {"query": "safe", "limit": 21}
            )
            invalid_id = await client.call_tool("get_passage", {"passage_id": "A" * 64})
        for result in (invalid_query, invalid_limit, invalid_id):
            assert result.is_error is True
            assert result.structured_content is None
            rendered = " ".join(getattr(item, "text", "") for item in result.content)
            assert "Traceback" not in rendered
            assert str(database) not in rendered

    _run(scenario())

    with pytest.raises(RuntimeError, match="Search database is unavailable"):
        create_server(tmp_path / "missing.sqlite3", _catalog_database(tmp_path))
    with pytest.raises(RuntimeError, match="Catalog database is unavailable"):
        create_server(database, tmp_path / "missing-catalog.sqlite3")


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

    with pytest.raises(RuntimeError, match="Search database is unavailable") as raised:
        _server(database, tmp_path)

    assert "escape.vtt" not in str(raised.value)


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
        "SECRET_VIDEO_QUERY",
        "SECRET_CORPUS_VALUE",
    }

    async def scenario() -> list[object]:
        async with Client(_server(database, tmp_path)) as client:
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
                await client.call_tool(
                    "search_videos", {"query": ["SECRET_VIDEO_QUERY"]}
                ),
                await client.call_tool(
                    "list_corpora", {"unexpected": "SECRET_CORPUS_VALUE"}
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
        armed = False

        def status(self):
            report = super().status()
            self.armed = True
            return report

        def _require_database_identity(
            self, expected: tuple[int, int, int, int, int]
        ) -> None:
            if self.armed:
                self.armed = False
                replacement = database.with_suffix(".replacement")
                replacement.write_bytes(database.read_bytes())
                os.replace(replacement, database)
            super()._require_database_identity(expected)

    monkeypatch.setattr(mcp_server, "SQLiteFtsIndex", ReplacingIndex)

    async def scenario() -> None:
        async with Client(
            mcp_server.create_server(database, _catalog_database(tmp_path))
        ) as client:
            result = await client.call_tool("get_passage", {"passage_id": "f" * 64})

        assert result.is_error is True
        rendered = result.model_dump_json(by_alias=True)
        assert "Search index is unavailable" in rendered
        assert "Passage was not found" not in rendered

    _run(scenario())


def test_main_prefers_explicit_databases_over_the_environment(
    tmp_path: Path, monkeypatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    explicit_search = tmp_path / "explicit-search.sqlite3"
    explicit_catalog = tmp_path / "explicit-catalog.sqlite3"
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(tmp_path / "environment.sqlite3"))
    monkeypatch.setenv(
        "YT_INSIGHTS_CATALOG_DATABASE", str(tmp_path / "environment-catalog.sqlite3")
    )
    captured: dict[str, object] = {}

    class FakeServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    monkeypatch.setattr(
        mcp_server,
        "create_server",
        lambda search_database, catalog_database: captured.update(
            search_database=search_database, catalog_database=catalog_database
        )
        or FakeServer(),
    )

    mcp_server.main(explicit_search, explicit_catalog)

    assert captured == {
        "search_database": explicit_search,
        "catalog_database": explicit_catalog,
        "transport": "stdio",
    }


def test_main_uses_the_environment_then_the_configured_data_root(tmp_path: Path, monkeypatch) -> None:
    import yt_insights.mcp_server as mcp_server
    from yt_insights import config as config_module

    captured: list[tuple[Path, Path]] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            assert transport == "stdio"

    monkeypatch.setattr(
        mcp_server,
        "create_server",
        lambda search_database, catalog_database: captured.append(
            (search_database, catalog_database)
        )
        or FakeServer(),
    )
    environment_search = tmp_path / "environment-search.sqlite3"
    environment_catalog = tmp_path / "environment-catalog.sqlite3"
    config_path = tmp_path / "config.toml"
    data_root = tmp_path / "configured-corpus"
    config_path.write_text(f'data_root = "{data_root}"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(environment_search))
    monkeypatch.setenv("YT_INSIGHTS_CATALOG_DATABASE", str(environment_catalog))
    mcp_server.main()
    monkeypatch.delenv("YT_INSIGHTS_SEARCH_DATABASE")
    monkeypatch.delenv("YT_INSIGHTS_CATALOG_DATABASE")
    mcp_server.main()

    assert captured == [
        (environment_search, environment_catalog),
        (
            data_root / ".search" / "search-v1.sqlite3",
            data_root / "catalog.sqlite3",
        ),
    ]


def test_main_does_not_load_config_when_both_database_paths_are_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    captured: list[tuple[Path, Path]] = []

    class FakeServer:
        def run(self, transport: str) -> None:
            assert transport == "stdio"

    monkeypatch.setattr(
        mcp_server,
        "create_server",
        lambda search_database, catalog_database: captured.append(
            (search_database, catalog_database)
        )
        or FakeServer(),
    )
    monkeypatch.setattr(
        mcp_server,
        "load_config",
        lambda _overrides: pytest.fail("configuration must not be loaded"),
    )
    explicit_search = tmp_path / "explicit-search.sqlite3"
    explicit_catalog = tmp_path / "explicit-catalog.sqlite3"
    mcp_server.main(explicit_search, explicit_catalog)

    environment_search = tmp_path / "environment-search.sqlite3"
    environment_catalog = tmp_path / "environment-catalog.sqlite3"
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(environment_search))
    monkeypatch.setenv("YT_INSIGHTS_CATALOG_DATABASE", str(environment_catalog))
    mcp_server.main()

    assert captured == [
        (explicit_search, explicit_catalog),
        (environment_search, environment_catalog),
    ]


def test_main_turns_required_config_failure_into_one_safe_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    canary = "SECRET_TOML_PARSE_CANARY"
    monkeypatch.setenv(
        "YT_INSIGHTS_SEARCH_DATABASE", str(tmp_path / "environment-search.sqlite3")
    )
    monkeypatch.delenv("YT_INSIGHTS_CATALOG_DATABASE", raising=False)
    monkeypatch.setattr(
        mcp_server,
        "load_config",
        lambda _overrides: (_ for _ in ()).throw(ValueError(canary)),
    )

    with pytest.raises(RuntimeError, match="configuration is unavailable") as raised:
        mcp_server.main()

    assert canary not in str(raised.value)


def test_entrypoint_sanitizes_a_malformed_required_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from yt_insights import config as config_module
    from yt_insights import mcp_entrypoint

    canary = "SECRET_MALFORMED_TOML_CANARY"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f"{canary} = [", encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)
    monkeypatch.setenv(
        "YT_INSIGHTS_SEARCH_DATABASE", str(tmp_path / "search.sqlite3")
    )
    monkeypatch.delenv("YT_INSIGHTS_CATALOG_DATABASE", raising=False)

    with pytest.raises(SystemExit) as raised:
        mcp_entrypoint.main()

    captured = capsys.readouterr()
    assert raised.value.code == 2
    assert "configuration is unavailable" in captured.err
    assert canary not in captured.err
    assert "Traceback" not in captured.err


def test_explicit_invalid_environment_database_never_falls_back_to_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_insights.mcp_server as mcp_server

    search_database, _passage = _indexed_passage(tmp_path)
    catalog_database = _catalog_database(tmp_path)
    monkeypatch.setenv("YT_INSIGHTS_SEARCH_DATABASE", str(search_database))
    monkeypatch.setenv("YT_INSIGHTS_CATALOG_DATABASE", "relative-catalog.sqlite3")

    with pytest.raises(RuntimeError, match="Catalog database must be an absolute path"):
        mcp_server.main()

    assert catalog_database.exists()


def test_catalog_semantic_corruption_returns_safe_tool_errors(
    tmp_path: Path,
) -> None:
    import sqlite3
    from yt_insights.mcp_server import create_server

    search_database, _passage = _indexed_passage(tmp_path)
    catalog_database = tmp_path / "catalog.sqlite3"
    with Catalog(catalog_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@safe-source/videos",
            VideoListResult(
                videos=[
                    VideoInfo(
                        video_id="safe123ABCD",
                        title="Safe metadata needle",
                        upload_date="20260828",
                    )
                ],
                errors=[],
                returncode=0,
            ),
        )
        catalog.checkpoint()

    source_canary = str(tmp_path / "SECRET_ABSOLUTE_SOURCE")
    watch_canary = str(tmp_path / "SECRET_ABSOLUTE_WATCH_URL")
    with sqlite3.connect(catalog_database) as connection:
        connection.execute("UPDATE video_sources SET source_slug = ?", (source_canary,))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    async def list_scenario() -> object:
        async with Client(create_server(search_database, catalog_database)) as client:
            return await client.call_tool("list_corpora", {})

    corpora = _run(list_scenario())
    with sqlite3.connect(catalog_database) as connection:
        connection.execute(
            "UPDATE video_sources SET source_slug = 'safe-source'"
        )
        connection.execute("UPDATE videos SET watch_url = ?", (watch_canary,))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    async def search_scenario() -> object:
        async with Client(create_server(search_database, catalog_database)) as client:
            return await client.call_tool(
                "search_videos", {"query": "safe metadata", "limit": 10}
            )

    videos = _run(search_scenario())

    for result in (corpora, videos):
        assert result.is_error is True  # type: ignore[union-attr]
        rendered = result.model_dump_json(by_alias=True)  # type: ignore[union-attr]
        assert source_canary not in rendered
        assert watch_canary not in rendered
        assert str(tmp_path) not in rendered
        assert "Traceback" not in rendered
