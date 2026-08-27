from __future__ import annotations

from pathlib import Path

from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import (
    DocumentRef,
    Passage,
    SearchQuery,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)


def test_service_returns_the_index_search_result(tmp_path: Path) -> None:
    from yt_insights.search.service import SearchService
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

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
        passage_id=compute_passage_id(document_id, 0, 0.0, 4.0, "delegated search"),
        document_id=document_id,
        ordinal=0,
        start_seconds=0.0,
        end_seconds=4.0,
        text="delegated search",
        youtube_url=youtube_url(document.video_id, 0.0),
    )
    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(
        CorpusManifest(
            documents=(document,),
            passages=(passage,),
            invalid_sources=(),
            sources_discovered=1,
            sources_selected=1,
            sources_invalid=0,
        )
    )

    assert SearchService(index).search(SearchQuery("delegated")) == index.search(SearchQuery("delegated"))
