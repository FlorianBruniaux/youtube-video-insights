from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from yt_insights.catalog import Catalog
from yt_insights.downloader import VideoInfo, VideoListResult
from yt_insights.research.assessment import (
    AssessmentRetryableError,
    SQLiteEvidenceReader,
    assess_local,
)
from yt_insights.research.models import (
    DatabaseSnapshot,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    VideoEvidence,
)
from yt_insights.search.corpus import CorpusManifest
from yt_insights.search.models import (
    DocumentRef,
    Passage,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)
from yt_insights.search.sqlite_fts import SQLiteFtsIndex

VIDEO_A = "VideoOne123"
VIDEO_B = "VideoTwo456"
NOW = datetime(2026, 8, 31, 12, tzinfo=UTC)


def _passage(
    *,
    query: str,
    passage_id: str,
    video_id: str = VIDEO_A,
    channel_id: str = "UC12345678901234567890AB",
    rank: int = 1,
) -> PassageEvidence:
    return PassageEvidence(
        query=query,
        passage_id=passage_id,
        video_id=video_id,
        channel_id=channel_id,
        rank=rank,
        url=f"https://youtube.com/watch?v={video_id}&t=12s",
        excerpt="bounded local evidence",
        source_sha256="a" * 64,
    )


def _video(
    *,
    query: str,
    video_id: str = VIDEO_A,
    published_at: date | None = date(2026, 8, 30),
    rank: int = 1,
    source_keys: tuple[str, ...] = ("inbox",),
) -> VideoEvidence:
    return VideoEvidence(
        query=query,
        video_id=video_id,
        source_keys=source_keys,
        title="Local evidence video",
        published_at=published_at,
        rank=rank,
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
    )


class FakeEvidenceReader:
    def __init__(
        self,
        *,
        passages: dict[str, tuple[PassageEvidence, ...]] | None = None,
        videos: dict[str, tuple[VideoEvidence, ...]] | None = None,
        changed: bool = False,
    ) -> None:
        self.passages = passages or {}
        self.videos = videos or {}
        self.changed = changed
        self.snapshot = DatabaseSnapshot("search-generation", "catalog-generation")
        self.passage_calls: list[tuple[str, tuple[str, ...], int]] = []
        self.video_calls: list[tuple[str, int]] = []

    def capture_snapshot(self) -> DatabaseSnapshot:
        return self.snapshot

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None:
        if self.changed or snapshot != self.snapshot:
            raise AssessmentRetryableError("local evidence changed during assessment")

    def search_passages(
        self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
    ) -> tuple[PassageEvidence, ...]:
        self.passage_calls.append((query.text, languages, limit))
        return self.passages.get(query.text, ())

    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]:
        self.video_calls.append((query.text, limit))
        return self.videos.get(query.text, ())


@pytest.mark.parametrize(
    ("profile", "days"),
    [
        (FreshnessProfile.FAST, 14),
        (FreshnessProfile.STANDARD, 30),
        (FreshnessProfile.STABLE, 90),
    ],
)
def test_assessment_treats_the_exact_freshness_boundary_as_fresh(
    profile: FreshnessProfile, days: int
) -> None:
    assessment = assess_local(
        queries=(QuerySpec("local inference"),),
        profile=profile,
        evidence_reader=FakeEvidenceReader(),
        last_successful_discovery_at=NOW - timedelta(days=days),
        now=NOW,
    )

    assert assessment.freshness.stale is False
    assert assessment.freshness.reason == "fresh"


def test_assessment_marks_a_microsecond_past_fast_boundary_as_stale() -> None:
    assessment = assess_local(
        queries=(QuerySpec("local inference"),),
        profile=FreshnessProfile.FAST,
        evidence_reader=FakeEvidenceReader(),
        last_successful_discovery_at=NOW - timedelta(days=14, microseconds=1),
        now=NOW,
    )

    assert assessment.freshness.stale is True
    assert assessment.freshness.reason == "stale"


def test_assessment_reports_never_checked_and_historical_refresh_exemption() -> None:
    never_checked = assess_local(
        queries=(QuerySpec("local inference"),),
        profile=FreshnessProfile.FAST,
        evidence_reader=FakeEvidenceReader(),
        last_successful_discovery_at=None,
        now=NOW,
    )
    historical = assess_local(
        queries=(QuerySpec("local inference"),),
        profile=FreshnessProfile.HISTORICAL,
        evidence_reader=FakeEvidenceReader(),
        last_successful_discovery_at=None,
        now=NOW,
    )

    assert (never_checked.freshness.stale, never_checked.freshness.reason) == (True, "never_checked")
    assert (historical.freshness.stale, historical.freshness.reason) == (False, "refresh_not_required")


@pytest.mark.parametrize(
    ("now", "last_successful_discovery_at"),
    [
        (datetime(2026, 8, 31, 12), None),
        (
            NOW,
            datetime(2026, 8, 31, 14, tzinfo=timezone(timedelta(hours=2))),
        ),
    ],
)
def test_assessment_rejects_non_utc_timestamps(
    now: datetime, last_successful_discovery_at: datetime | None
) -> None:
    with pytest.raises(ValueError, match="UTC"):
        assess_local(
            queries=(QuerySpec("local inference"),),
            profile=FreshnessProfile.FAST,
            evidence_reader=FakeEvidenceReader(),
            last_successful_discovery_at=last_successful_discovery_at,
            now=now,
        )


def test_assessment_aggregates_unique_evidence_and_preserves_real_channel_boundary() -> None:
    first = QuerySpec("local inference")
    second = QuerySpec("edge inference")
    reader = FakeEvidenceReader(
        passages={
            first.text: (_passage(query=first.text, passage_id="passage-a", rank=2),),
            second.text: (
                _passage(query=second.text, passage_id="passage-a", rank=1),
                _passage(query=second.text, passage_id="passage-b", video_id=VIDEO_B, rank=2),
            ),
        },
        videos={
            first.text: (_video(query=first.text, video_id=VIDEO_A),),
            second.text: (
                _video(query=second.text, video_id=VIDEO_A, rank=2),
                _video(query=second.text, video_id=VIDEO_B, published_at=None),
            ),
        },
    )

    assessment = assess_local(
        queries=(first, second),
        profile=FreshnessProfile.FAST,
        evidence_reader=reader,
        last_successful_discovery_at=NOW - timedelta(days=14),
        now=NOW,
        languages=("en", "fr"),
    )

    assert [item.passage_id for item in assessment.passages] == ["passage-a", "passage-b"]
    assert assessment.passages[0].query == second.text
    assert [item.video_id for item in assessment.videos] == [VIDEO_A, VIDEO_B]
    assert assessment.coverage.matched_passages == 2
    assert assessment.coverage.matched_videos == 2
    assert assessment.coverage.distinct_channels == 1
    assert assessment.coverage.newest_source_published_at == date(2026, 8, 30)
    assert assessment.coverage.unknown_publication_date_count == 1
    assert reader.passage_calls == [
        (first.text, ("en", "fr"), 20),
        (second.text, ("en", "fr"), 20),
    ]
    assert reader.video_calls == [(first.text, 20), (second.text, 20)]


def test_assessment_marks_a_query_empty_only_when_both_evidence_sets_are_empty() -> None:
    passage_query = QuerySpec("passage evidence")
    empty_query = QuerySpec("no evidence")
    assessment = assess_local(
        queries=(passage_query, empty_query),
        profile=FreshnessProfile.FAST,
        evidence_reader=FakeEvidenceReader(
            passages={
                passage_query.text: (_passage(query=passage_query.text, passage_id="passage-a"),),
            }
        ),
        last_successful_discovery_at=None,
        now=NOW,
    )

    assert assessment.coverage.queries_with_zero_hits == (empty_query.text,)


def test_assessment_does_not_mark_a_video_only_query_as_empty() -> None:
    query = QuerySpec("catalogue evidence")
    assessment = assess_local(
        queries=(query,),
        profile=FreshnessProfile.FAST,
        evidence_reader=FakeEvidenceReader(videos={query.text: (_video(query=query.text),)}),
        last_successful_discovery_at=None,
        now=NOW,
    )

    assert assessment.coverage.queries_with_zero_hits == ()


def test_assessment_does_not_return_mixed_evidence_when_a_snapshot_changes() -> None:
    with pytest.raises(AssessmentRetryableError, match="changed"):
        assess_local(
            queries=(QuerySpec("local inference"),),
            profile=FreshnessProfile.FAST,
            evidence_reader=FakeEvidenceReader(changed=True),
            last_successful_discovery_at=None,
            now=NOW,
        )


def test_assessment_excludes_provenance_slugs_that_are_not_youtube_channel_ids() -> None:
    query = QuerySpec("local inference")
    assessment = assess_local(
        queries=(query,),
        profile=FreshnessProfile.FAST,
        evidence_reader=FakeEvidenceReader(
            passages={
                query.text: (_passage(query=query.text, passage_id="passage-a", channel_id="inbox"),)
            }
        ),
        last_successful_discovery_at=None,
        now=NOW,
    )

    assert assessment.coverage.distinct_channels == 0


def _build_local_databases(tmp_path: Path) -> tuple[Path, Path]:
    search_database = tmp_path / "search.sqlite3"
    catalog_database = tmp_path / "catalog.sqlite3"
    documents: list[DocumentRef] = []
    passages: list[Passage] = []
    for video_id, language, text, source_hash in (
        (VIDEO_A, "en", "local inference evidence", "a" * 64),
        (VIDEO_B, "fr", "local inference evidence francais", "b" * 64),
    ):
        document_id = compute_document_id("UC12345678901234567890AB", video_id, language)
        documents.append(
            DocumentRef(
                document_id=document_id,
                source_relpath=f"channel/transcripts/Local [{video_id}].{language}.vtt",
                source_sha256=source_hash,
                channel_id="UC12345678901234567890AB",
                channel_title="Verified channel",
                video_id=video_id,
                video_title="Local inference evidence",
                language=language,
            )
        )
        passages.append(
            Passage(
                passage_id=compute_passage_id(document_id, 0, 12.0, 18.0, text),
                document_id=document_id,
                ordinal=0,
                start_seconds=12.0,
                end_seconds=18.0,
                text=text,
                youtube_url=youtube_url(video_id, 12.0),
            )
        )
    SQLiteFtsIndex(search_database).rebuild(
        CorpusManifest(
            documents=tuple(documents),
            passages=tuple(passages),
            invalid_sources=(),
            sources_discovered=2,
            sources_selected=2,
            sources_invalid=0,
        )
    )
    with Catalog(catalog_database) as catalog:
        catalog.ingest_discovery(
            "https://www.youtube.com/@inbox/videos",
            VideoListResult(
                videos=[
                    VideoInfo(VIDEO_A, "Local inference evidence", "20260830"),
                    VideoInfo(VIDEO_B, "Local inference evidence", "unknown"),
                ],
                errors=[],
                returncode=0,
            ),
        )
        catalog.checkpoint()
    return search_database, catalog_database


def test_sqlite_reader_maps_bounded_search_and_catalogue_evidence_without_mutation(
    tmp_path: Path,
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    before_search = sha256(search_database.read_bytes()).hexdigest()
    before_catalogue = sha256(catalog_database.read_bytes()).hexdigest()
    reader = SQLiteEvidenceReader(
        search_database=search_database, catalog_database=catalog_database
    )
    query = QuerySpec("local inference")

    passages = reader.search_passages(query, languages=("fr", "en"), limit=20)
    one_language_passages = reader.search_passages(query, languages=("en",), limit=20)
    videos = reader.search_videos(query, limit=20)

    assert [item.video_id for item in passages] == [VIDEO_B, VIDEO_A]
    assert passages[0].passage_id
    assert passages[0].url == f"https://youtube.com/watch?v={VIDEO_B}&t=12s"
    assert passages[0].excerpt == "local inference evidence francais"
    assert passages[0].source_sha256 == "b" * 64
    assert [item.video_id for item in one_language_passages] == [VIDEO_A]
    assert [(item.video_id, item.published_at, item.source_keys) for item in videos] == [
        (VIDEO_A, date(2026, 8, 30), ("inbox",)),
        (VIDEO_B, None, ("inbox",)),
    ]
    assert sha256(search_database.read_bytes()).hexdigest() == before_search
    assert sha256(catalog_database.read_bytes()).hexdigest() == before_catalogue


def test_sqlite_reader_opens_the_search_database_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    original_connect = sqlite3.connect
    opened: list[tuple[object, dict[str, object]]] = []

    def capture_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        opened.append((args[0], kwargs))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", capture_connect)
    SQLiteEvidenceReader(
        search_database=search_database, catalog_database=catalog_database
    ).search_passages(QuerySpec("local inference"), languages=(), limit=20)

    assert (
        search_database.absolute().as_uri() + "?mode=ro",
        {"uri": True},
    ) in opened


@pytest.mark.parametrize("replace_search", [True, False])
def test_sqlite_reader_rejects_either_replaced_database_after_multi_query_assessment(
    tmp_path: Path, replace_search: bool
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    database = search_database if replace_search else catalog_database

    class ReplacingReader(SQLiteEvidenceReader):
        def _replace_database(self) -> None:
            replacement = database.with_name(f"{database.stem}-replacement.sqlite3")
            shutil.copyfile(database, replacement)
            os.replace(replacement, database)

        def search_passages(
            self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
        ) -> tuple[PassageEvidence, ...]:
            results = super().search_passages(query, languages=languages, limit=limit)
            self._replace_database()
            return results

        def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]:
            return super().search_videos(query, limit=limit)

    with pytest.raises(AssessmentRetryableError, match="changed"):
        assess_local(
            queries=(QuerySpec("local inference"), QuerySpec("evidence")),
            profile=FreshnessProfile.FAST,
            evidence_reader=ReplacingReader(
                search_database=search_database, catalog_database=catalog_database
            ),
            last_successful_discovery_at=None,
            now=NOW,
        )


@pytest.mark.parametrize("invalid", [False, True])
def test_sqlite_reader_reports_missing_or_invalid_search_database_without_query_text(
    tmp_path: Path, invalid: bool
) -> None:
    _search_database, catalog_database = _build_local_databases(tmp_path)
    database = tmp_path / ("invalid.sqlite3" if invalid else "missing.sqlite3")
    if invalid:
        database.write_bytes(b"not sqlite")
    reader = SQLiteEvidenceReader(
        search_database=database,
        catalog_database=catalog_database,
    )

    with pytest.raises(AssessmentRetryableError) as raised:
        reader.search_passages(QuerySpec("private query text"), languages=(), limit=20)

    assert "private query text" not in str(raised.value)


def test_sqlite_reader_reports_invalid_catalogue_without_query_text(tmp_path: Path) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    catalog_database.write_bytes(b"not sqlite")
    reader = SQLiteEvidenceReader(
        search_database=search_database, catalog_database=catalog_database
    )

    with pytest.raises(AssessmentRetryableError) as raised:
        reader.search_videos(QuerySpec("private query text"), limit=20)

    assert "private query text" not in str(raised.value)


def test_multi_query_assessment_keeps_one_catalogue_snapshot_and_closes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    original_open = Catalog.open_read_only
    opened = 0
    closed = 0

    class CountingCatalog:
        def __init__(self, catalog: object) -> None:
            self._catalog = catalog

        def __enter__(self) -> object:
            return self._catalog.__enter__()  # type: ignore[union-attr]

        def __exit__(self, *args: object) -> None:
            nonlocal closed
            closed += 1
            self._catalog.__exit__(*args)  # type: ignore[union-attr]

    def open_once(path: Path) -> CountingCatalog:
        nonlocal opened
        opened += 1
        return CountingCatalog(original_open(path))

    monkeypatch.setattr(Catalog, "open_read_only", staticmethod(open_once))
    assessment = assess_local(
        queries=(QuerySpec("local inference"), QuerySpec("evidence")),
        profile=FreshnessProfile.FAST,
        evidence_reader=SQLiteEvidenceReader(
            search_database=search_database, catalog_database=catalog_database
        ),
        last_successful_discovery_at=None,
        now=NOW,
    )

    assert opened == closed == 1
    assert [item.video_id for item in assessment.videos] == [VIDEO_A, VIDEO_B]


def test_assessment_scope_closes_catalogue_when_evidence_collection_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    original_open = Catalog.open_read_only
    closed = 0

    class CountingCatalog:
        def __init__(self, catalog: object) -> None:
            self._catalog = catalog

        def __enter__(self) -> object:
            return self._catalog.__enter__()  # type: ignore[union-attr]

        def __exit__(self, *args: object) -> None:
            nonlocal closed
            closed += 1
            self._catalog.__exit__(*args)  # type: ignore[union-attr]

    class FailingReader(SQLiteEvidenceReader):
        def search_passages(
            self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
        ) -> tuple[PassageEvidence, ...]:
            raise AssessmentRetryableError("forced evidence failure")

    monkeypatch.setattr(
        Catalog,
        "open_read_only",
        staticmethod(lambda path: CountingCatalog(original_open(path))),
    )

    with pytest.raises(AssessmentRetryableError, match="forced evidence failure"):
        assess_local(
            queries=(QuerySpec("local inference"),),
            profile=FreshnessProfile.FAST,
            evidence_reader=FailingReader(
                search_database=search_database, catalog_database=catalog_database
            ),
            last_successful_discovery_at=None,
            now=NOW,
        )

    assert closed == 1


@pytest.mark.parametrize("invalid", [False, True])
def test_assessment_scope_bounds_missing_or_invalid_catalogue_errors(
    tmp_path: Path, invalid: bool
) -> None:
    search_database, catalog_database = _build_local_databases(tmp_path)
    if invalid:
        catalog_database.write_bytes(b"not sqlite")
    else:
        catalog_database.unlink()
    query = QuerySpec("private assessment query")

    with pytest.raises(AssessmentRetryableError) as raised:
        assess_local(
            queries=(query,),
            profile=FreshnessProfile.FAST,
            evidence_reader=SQLiteEvidenceReader(
                search_database=search_database, catalog_database=catalog_database
            ),
            last_successful_discovery_at=None,
            now=NOW,
        )

    assert str(raised.value) in {
        "local evidence database is unavailable",
        "local catalogue evidence is unavailable",
    }
    assert query.text not in str(raised.value)
    assert str(catalog_database) not in str(raised.value)
