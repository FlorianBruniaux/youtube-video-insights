from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from yt_insights.search.corpus import CorpusManifest, InvalidSource
from yt_insights.search.models import (
    DocumentRef,
    Passage,
    SearchQuery,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)


def _manifest(*, title: str = "Search video", text: str = "safe retrieval systems") -> CorpusManifest:
    document_id = compute_document_id("channel-a", "VideoId_123", "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath="channel-a/transcripts/Search video [VideoId_123].en.vtt",
        source_sha256="a" * 64,
        channel_id="channel-a",
        channel_title="Channel A",
        video_id="VideoId_123",
        video_title=title,
        language="en",
    )
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 12.5, 18.0, text),
        document_id=document_id,
        ordinal=0,
        start_seconds=12.5,
        end_seconds=18.0,
        text=text,
        youtube_url=youtube_url(document.video_id, 12.5),
    )
    return CorpusManifest(
        documents=(document,),
        passages=(passage,),
        invalid_sources=(InvalidSource("broken/file.vtt", "parse_error"),),
        sources_discovered=2,
        sources_selected=1,
        sources_invalid=1,
    )


def _add_document(manifest: CorpusManifest, *, channel: str, language: str, text: str) -> CorpusManifest:
    video_id = "SecondVid12"
    document_id = compute_document_id(channel, video_id, language)
    document = DocumentRef(
        document_id=document_id,
        source_relpath=f"{channel}/transcripts/Second [{video_id}].{language}.vtt",
        source_sha256="b" * 64,
        channel_id=channel,
        channel_title=channel.title(),
        video_id=video_id,
        video_title="Second video",
        language=language,
    )
    passage = Passage(
        passage_id=compute_passage_id(document_id, 0, 30.0, 34.0, text),
        document_id=document_id,
        ordinal=0,
        start_seconds=30.0,
        end_seconds=34.0,
        text=text,
        youtube_url=youtube_url(video_id, 30.0),
    )
    return CorpusManifest(
        documents=manifest.documents + (document,),
        passages=manifest.passages + (passage,),
        invalid_sources=manifest.invalid_sources,
        sources_discovered=3,
        sources_selected=2,
        sources_invalid=1,
    )


def test_rebuild_publishes_valid_schema_metadata_and_status(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    database = tmp_path / "nested" / "search.sqlite3"
    index = SQLiteFtsIndex(database)

    report = index.rebuild(_manifest())

    assert report.documents_indexed == 1
    assert report.passages_indexed == 1
    assert report.invalid_sources == ("broken/file.vtt",)
    assert index.status() == report
    with sqlite3.connect(database) as connection:
        meta = dict(connection.execute("SELECT key, value FROM index_meta"))
        assert meta["schema_version"] == "1"
        assert meta["index_version"] == "search-v1"
        assert meta["invalid_sources"] == '["broken/file.vtt"]'
        assert connection.execute("SELECT count(*) FROM passages_fts").fetchone()[0] == 1


def test_search_returns_ranked_timestamped_hits_with_exact_filters(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    manifest = _add_document(_manifest(text="safe retrieval systems"), channel="channel-b", language="fr", text="safe retrieval")
    index.rebuild(manifest)

    hits = index.search(SearchQuery("safe retrieval", channel="channel-b", language="fr"))

    assert len(hits) == 1
    assert hits[0].rank == 1
    assert hits[0].score >= 0
    assert hits[0].document.channel_id == "channel-b"
    assert hits[0].passage.start_seconds == 30.0
    assert hits[0].passage.youtube_url.endswith("&t=30s")


def test_failed_publish_leaves_prior_active_database_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from yt_insights.search import sqlite_fts
    from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex

    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest(text="first corpus"))

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(sqlite_fts.os, "replace", fail_replace)
    with pytest.raises(SearchIndexError):
        index.rebuild(_manifest(text="replacement corpus"))

    assert [hit.passage.text for hit in index.search(SearchQuery("first"))] == ["first corpus"]


def test_missing_and_invalid_databases_raise_domain_errors(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SearchIndexNotFound, SQLiteFtsIndex

    missing = SQLiteFtsIndex(tmp_path / "missing.sqlite3")
    with pytest.raises(SearchIndexNotFound):
        missing.status()

    invalid_path = tmp_path / "invalid.sqlite3"
    with sqlite3.connect(invalid_path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(SearchIndexInvalid):
        SQLiteFtsIndex(invalid_path).status()


def test_status_rejects_a_schema_missing_required_constraints(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "underconstrained.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT, source_relpath TEXT, source_sha256 TEXT, channel_id TEXT,
                channel_title TEXT, video_id TEXT, video_title TEXT, language TEXT
            );
            CREATE TABLE passages (
                passage_id TEXT, document_id TEXT, ordinal INTEGER, start_seconds REAL,
                end_seconds REAL, text TEXT, youtube_url TEXT
            );
            CREATE VIRTUAL TABLE passages_fts USING fts5(
                passage_id UNINDEXED, video_title, text,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            CREATE TABLE index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        connection.executemany(
            "INSERT INTO index_meta (key, value) VALUES (?, ?)",
            [
                ("schema_version", "1"), ("index_version", "search-v1"),
                ("sources_discovered", "0"), ("sources_selected", "0"),
                ("sources_invalid", "0"), ("documents_indexed", "0"),
                ("passages_indexed", "0"), ("invalid_sources", "[]"),
            ],
        )

    with pytest.raises(SearchIndexInvalid):
        SQLiteFtsIndex(database).status()


def test_search_rejects_invalid_persisted_rows(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest())
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE passages SET youtube_url = 'https://invalid.example'")
        connection.commit()

    with pytest.raises(SearchIndexInvalid):
        index.search(SearchQuery("retrieval"))
