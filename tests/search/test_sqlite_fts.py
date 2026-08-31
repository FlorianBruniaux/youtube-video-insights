from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from itertools import pairwise
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

_GENERATION_ID_RE = re.compile(r"[0-9a-f]{32}")


def _generation_id(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        generation_id = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'generation_id'"
        ).fetchone()[0]
    assert _GENERATION_ID_RE.fullmatch(generation_id)
    return generation_id


def _receipt_path(database: Path, generation_id: str) -> Path:
    return database.with_name(f".{database.name}.{generation_id}.receipt.json")


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
        documents=(*manifest.documents, document),
        passages=(*manifest.passages, passage),
        invalid_sources=manifest.invalid_sources,
        sources_discovered=3,
        sources_selected=2,
        sources_invalid=1,
    )


def _large_manifest(passage_count: int) -> CorpusManifest:
    document_id = compute_document_id("channel-scale", "ScaleVid_12", "en")
    document = DocumentRef(
        document_id=document_id,
        source_relpath="channel-scale/transcripts/Scale [ScaleVid_12].en.vtt",
        source_sha256="c" * 64,
        channel_id="channel-scale",
        channel_title="Scale channel",
        video_id="ScaleVid_12",
        video_title="Scale video",
        language="en",
    )
    passages = tuple(
        Passage(
            passage_id=compute_passage_id(
                document_id,
                ordinal,
                float(ordinal),
                float(ordinal + 1),
                f"bounded validation passage {ordinal}",
            ),
            document_id=document_id,
            ordinal=ordinal,
            start_seconds=float(ordinal),
            end_seconds=float(ordinal + 1),
            text=f"bounded validation passage {ordinal}",
            youtube_url=youtube_url(document.video_id, float(ordinal)),
        )
        for ordinal in range(passage_count)
    )
    return CorpusManifest(
        documents=(document,),
        passages=passages,
        invalid_sources=(),
        sources_discovered=1,
        sources_selected=1,
        sources_invalid=0,
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
        generation_id = meta["generation_id"]
        assert _GENERATION_ID_RE.fullmatch(generation_id)
        assert meta["invalid_sources"] == '["broken/file.vtt"]'
        assert connection.execute("SELECT count(*) FROM passages_fts").fetchone()[0] == 1
    receipt_path = _receipt_path(database, generation_id)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt == {
        "generation_id": generation_id,
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
    }


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


def test_get_passage_returns_the_validated_document_and_passage_without_fts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    manifest = _manifest(text="exact passage lookup")
    index = SQLiteFtsIndex(database)
    index.rebuild(manifest)

    original_connect = sqlite3.connect

    def connect_without_fts(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        connection.set_authorizer(
            lambda action, table, _column, _db, _trigger: (
                sqlite3.SQLITE_DENY if table == "passages_fts" else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect_without_fts)

    assert index.get_passage(manifest.passages[0].passage_id) == (
        manifest.documents[0],
        manifest.passages[0],
    )


def test_get_passage_rejects_an_unknown_identifier(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SearchPassageNotFound, SQLiteFtsIndex

    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest())

    with pytest.raises(SearchPassageNotFound, match="passage does not exist"):
        index.get_passage("f" * 64)


def test_get_passage_rejects_database_replacement_before_reporting_not_found(
    tmp_path: Path,
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    SQLiteFtsIndex(database).rebuild(_manifest())

    class ReplacingIndex(SQLiteFtsIndex):
        def _require_database_identity(
            self, expected: tuple[int, int, int, int, int]
        ) -> None:
            replacement = database.with_suffix(".replacement")
            replacement.write_bytes(database.read_bytes())
            os.replace(replacement, database)
            super()._require_database_identity(expected)

    with pytest.raises(SearchIndexInvalid, match="changed during access"):
        ReplacingIndex(database).get_passage("f" * 64)


def test_search_in_a_new_process_uses_the_persisted_validation_receipt(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    SQLiteFtsIndex(database).rebuild(_manifest(text="needle published generation"))
    generation_id = _generation_id(database)
    assert _receipt_path(database, generation_id).is_file()
    source_root = Path(__file__).resolve().parents[2] / "src"
    child_code = """
from pathlib import Path
import sys
from yt_insights.search.models import SearchQuery
from yt_insights.search.sqlite_fts import SQLiteFtsIndex

def fail_exhaustive_validation(*args, **kwargs):
    raise AssertionError("search must not call the exhaustive validator")

SQLiteFtsIndex._validate_and_load_report = fail_exhaustive_validation
hits = SQLiteFtsIndex(Path(sys.argv[1])).search(SearchQuery("needle"))
assert [hit.passage.text for hit in hits] == ["needle published generation"]
"""
    environment = {**os.environ, "PYTHONPATH": str(source_root)}

    child = subprocess.run(
        [sys.executable, "-c", child_code, str(database)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert child.returncode == 0, child.stderr


def test_database_hash_is_cached_only_while_full_file_identity_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="needle"))
    original_hash = index._sha256_regular_file
    calls = 0

    def counted_hash(path: Path, identity: tuple[int, int, int, int, int]) -> str:
        nonlocal calls
        calls += 1
        return original_hash(path, identity)

    monkeypatch.setattr(index, "_sha256_regular_file", counted_hash)

    assert index.search(SearchQuery("needle"))
    assert index.search(SearchQuery("needle"))
    assert calls == 1


@pytest.mark.parametrize("receipt_state", ["missing", "malformed", "symlink"])
def test_search_rejects_an_untrusted_generation_receipt(
    tmp_path: Path, receipt_state: str
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="needle"))
    receipt_path = _receipt_path(database, _generation_id(database))
    if receipt_state == "missing":
        receipt_path.unlink()
    elif receipt_state == "malformed":
        receipt_path.write_text("not json", encoding="utf-8")
    else:
        receipt_target = tmp_path / "receipt-target.json"
        receipt_target.write_text(receipt_path.read_text(encoding="utf-8"), encoding="utf-8")
        receipt_path.unlink()
        receipt_path.symlink_to(receipt_target)

    with pytest.raises(SearchIndexInvalid):
        index.search(SearchQuery("needle"))


def test_search_excludes_title_only_matches(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest(title="Needle in the repeated title", text="unrelated passage"))

    assert index.search(SearchQuery("needle")) == ()


@pytest.mark.parametrize(
    ("query", "text", "expected_terms"),
    [
        ("alpha omega", " ".join(["alpha"] + ["middle"] * 80 + ["omega"]), ("alpha", "omega")),
        ("café déjà", " ".join(["cafe"] + ["middle"] * 80 + ["deja"]), ("cafe", "deja")),
        ("alpha alpha omega", " ".join(["alpha"] + ["middle"] * 80 + ["omega"]), ("alpha", "omega")),
        ("alpha omega", "alpha nearby terms omega", ("alpha", "omega")),
        ("foo_bar", "prefix foo bar suffix", ("foo", "bar")),
    ],
)
def test_search_excerpt_covers_all_matched_terms_without_returning_a_long_passage(
    tmp_path: Path, query: str, text: str, expected_terms: tuple[str, ...]
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest(title="Unrelated title", text=text))

    (hit,) = index.search(SearchQuery(query))

    assert hit.passage.text == text
    assert all(term in hit.excerpt for term in expected_terms)
    if len(text.split()) > 24:
        assert hit.excerpt != text


def test_search_excerpt_uses_dynamic_markers_that_cannot_collide_with_passage_text(
    tmp_path: Path,
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    text = "prefix \x01 control needle suffix"
    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest(text=text))

    (hit,) = index.search(SearchQuery("needle"))

    assert "needle" in hit.excerpt
    assert hit.passage.text == text


def test_search_excerpt_distributes_bounded_windows_across_many_matches(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    terms = [f"term{index}" for index in range(10)]
    text = (" " + "middle " * 30).join(terms)
    index = SQLiteFtsIndex(tmp_path / "search.sqlite3")
    index.rebuild(_manifest(text=text))

    (hit,) = index.search(SearchQuery(" ".join(terms)))

    assert terms[0] in hit.excerpt
    assert terms[-1] in hit.excerpt
    assert "2 matched terms outside bounded excerpt" in hit.excerpt


@pytest.mark.parametrize("corruption", ["invalid_source_sha", "deleted_fts"])
def test_search_rejects_post_build_corruption_even_when_the_query_has_no_hit(
    tmp_path: Path, corruption: str
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="needle"))
    with sqlite3.connect(database) as connection:
        if corruption == "invalid_source_sha":
            connection.execute("UPDATE documents SET source_sha256 = 'invalid'")
        else:
            connection.execute("DELETE FROM passages_fts")
        connection.commit()

    with pytest.raises(SearchIndexInvalid):
        index.search(SearchQuery("absent"))


@pytest.mark.parametrize("operation", ["status", "search", "get_passage"])
def test_runtime_rejects_in_place_corruption_after_mtime_is_restored(
    tmp_path: Path, operation: str
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    manifest = _manifest(text="needle")
    index = SQLiteFtsIndex(database)
    index.rebuild(manifest)
    original = database.stat()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE documents SET source_relpath = '../escape.vtt'")
        connection.commit()
    os.utime(database, ns=(original.st_atime_ns, original.st_mtime_ns))

    with pytest.raises(SearchIndexInvalid):
        if operation == "status":
            index.status()
        elif operation == "search":
            index.search(SearchQuery("absent"))
        else:
            index.get_passage(manifest.passages[0].passage_id)


def test_rebuild_rejects_invalid_metadata_and_preserves_the_active_database(tmp_path: Path) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="active corpus"))
    valid = _manifest(text="invalid replacement")
    inconsistent = CorpusManifest(
        documents=valid.documents,
        passages=valid.passages,
        invalid_sources=valid.invalid_sources,
        sources_discovered=valid.sources_discovered,
        sources_selected=valid.sources_selected,
        sources_invalid=0,
    )

    with pytest.raises(SearchIndexInvalid):
        index.rebuild(inconsistent)

    assert [hit.passage.text for hit in index.search(SearchQuery("active"))] == ["active corpus"]


@pytest.mark.parametrize("operation", ["status", "search"])
def test_index_path_symlinks_are_rejected_before_sqlite_open(
    tmp_path: Path, operation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yt_insights.search import sqlite_fts
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "search.sqlite3"
    SQLiteFtsIndex(database).rebuild(_manifest(text="needle"))
    linked_database = tmp_path / "linked.sqlite3"
    linked_database.symlink_to(database)

    def fail_connect(*args, **kwargs):
        raise AssertionError("sqlite3.connect must not receive a symlink path")

    monkeypatch.setattr(sqlite_fts.sqlite3, "connect", fail_connect)

    with pytest.raises(SearchIndexInvalid, match="regular file"):
        if operation == "status":
            SQLiteFtsIndex(linked_database).status()
        else:
            SQLiteFtsIndex(linked_database).search(SearchQuery("needle"))


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
    from yt_insights.search.sqlite_fts import (
        SearchIndexInvalid,
        SearchIndexNotFound,
        SQLiteFtsIndex,
    )

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


@pytest.mark.parametrize("operation", ["status", "search"])
def test_runtime_rejects_nullable_business_columns_and_indexed_fts_identifier(
    tmp_path: Path, operation: str
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / "nullable-and-indexed-fts.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                document_id TEXT PRIMARY KEY,
                source_relpath TEXT UNIQUE,
                source_sha256 TEXT,
                channel_id TEXT,
                channel_title TEXT,
                video_id TEXT,
                video_title TEXT,
                language TEXT
            );
            CREATE TABLE passages (
                passage_id TEXT PRIMARY KEY,
                document_id TEXT REFERENCES documents(document_id) ON DELETE CASCADE,
                ordinal INTEGER,
                start_seconds REAL,
                end_seconds REAL,
                text TEXT,
                youtube_url TEXT,
                UNIQUE (document_id, ordinal)
            );
            CREATE VIRTUAL TABLE passages_fts USING fts5(
                passage_id, video_title, text,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            CREATE TABLE index_meta (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL);
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

    index = SQLiteFtsIndex(database)
    with pytest.raises(SearchIndexInvalid):
        if operation == "status":
            index.status()
        else:
            index.search(SearchQuery("needle"))


def _validation_progress_callbacks(passage_count: int) -> int:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    manifest = _large_manifest(passage_count)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    SQLiteFtsIndex._create_schema(connection)
    SQLiteFtsIndex._insert_manifest(connection, manifest)
    progress_callbacks = 0

    def count_progress() -> int:
        nonlocal progress_callbacks
        progress_callbacks += 1
        return 0

    connection.set_progress_handler(count_progress, 1_000)

    SQLiteFtsIndex._verify_built_database(
        connection, SQLiteFtsIndex._report_from_manifest(manifest)
    )
    connection.close()
    return progress_callbacks


def test_built_database_validation_grows_linearly_across_multiple_sizes() -> None:
    passage_counts = (250, 500, 1_000, 2_000)

    callbacks = tuple(
        _validation_progress_callbacks(passage_count)
        for passage_count in passage_counts
    )
    callbacks_per_passage = tuple(
        callback_count / passage_count
        for callback_count, passage_count in zip(callbacks, passage_counts, strict=True)
    )

    assert all(callback_count > 0 for callback_count in callbacks)
    assert max(callbacks_per_passage) <= min(callbacks_per_passage) * 1.5
    assert all(
        larger <= smaller * 2.6 + 10
        for smaller, larger in pairwise(callbacks)
    )


def test_public_apis_accept_search_v1_legacy_insertion_order_with_aligned_rowids(
    tmp_path: Path,
) -> None:
    from yt_insights.search.sqlite_fts import SQLiteFtsIndex

    manifest = _large_manifest(3)
    database = tmp_path / "legacy-search-v1.sqlite3"
    index = SQLiteFtsIndex(database)
    document = manifest.documents[0]
    generation_id = "d" * 32
    report = index._report_from_manifest(manifest)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        index._create_schema(connection)
        connection.execute(
            """
            INSERT INTO documents (
                document_id, source_relpath, source_sha256, channel_id, channel_title,
                video_id, video_title, language
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document.document_id,
                document.source_relpath,
                document.source_sha256,
                document.channel_id,
                document.channel_title,
                document.video_id,
                document.video_title,
                document.language,
            ),
        )
        connection.executemany(
            """
            INSERT INTO passages (
                passage_id, document_id, ordinal, start_seconds, end_seconds, text,
                youtube_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    passage.passage_id,
                    passage.document_id,
                    passage.ordinal,
                    passage.start_seconds,
                    passage.end_seconds,
                    passage.text,
                    passage.youtube_url,
                )
                for passage in manifest.passages
            ),
        )
        connection.executemany(
            "INSERT INTO passages_fts (passage_id, video_title, text) VALUES (?, ?, ?)",
            (
                (passage.passage_id, document.video_title, passage.text)
                for passage in manifest.passages
            ),
        )
        assert list(connection.execute("SELECT rowid FROM passages")) == list(
            connection.execute("SELECT rowid FROM passages_fts")
        )
        index._insert_metadata(connection, report, generation_id)
    index._write_generation_receipt(generation_id, database)

    assert index.status() == report
    hits = index.search(SearchQuery("bounded validation", limit=10))
    assert {hit.passage.passage_id for hit in hits} == {
        passage.passage_id for passage in manifest.passages
    }
    assert index.get_passage(manifest.passages[1].passage_id) == (
        document,
        manifest.passages[1],
    )


def test_built_database_validation_rejects_direct_fts_rowid_corruption() -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    manifest = _large_manifest(2)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    SQLiteFtsIndex._create_schema(connection)
    SQLiteFtsIndex._insert_manifest(connection, manifest)
    connection.execute("UPDATE passages_fts SET rowid = 999 WHERE rowid = 1")

    with pytest.raises(SearchIndexInvalid, match="FTS rows do not match passages"):
        SQLiteFtsIndex._verify_built_database(
            connection, SQLiteFtsIndex._report_from_manifest(manifest)
        )


def test_built_database_validation_rejects_reordered_fts_rowids() -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    manifest = _large_manifest(2)
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    SQLiteFtsIndex._create_schema(connection)
    SQLiteFtsIndex._insert_manifest(connection, manifest)
    connection.execute("UPDATE passages_fts SET rowid = 999 WHERE rowid = 1")
    connection.execute("UPDATE passages_fts SET rowid = 1 WHERE rowid = 2")
    connection.execute("UPDATE passages_fts SET rowid = 2 WHERE rowid = 999")

    with pytest.raises(SearchIndexInvalid, match="FTS rows do not match passages"):
        SQLiteFtsIndex._verify_built_database(
            connection, SQLiteFtsIndex._report_from_manifest(manifest)
        )


@pytest.mark.parametrize(
    "corruption", ["passage_id", "text", "video_title", "orphan_fts"]
)
def test_runtime_rejects_fts_rows_that_do_not_match_canonical_passages(
    tmp_path: Path, corruption: str
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / f"{corruption}.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="needle"))
    with sqlite3.connect(database) as connection:
        if corruption == "passage_id":
            connection.execute(
                "UPDATE passages_fts SET passage_id = ?", ("not-a-passage",)
            )
        elif corruption == "text":
            connection.execute("UPDATE passages_fts SET text = ?", ("tampered text",))
        elif corruption == "video_title":
            connection.execute(
                "UPDATE passages_fts SET video_title = ?", ("tampered title",)
            )
        else:
            connection.execute(
                """
                INSERT INTO passages_fts (rowid, passage_id, video_title, text)
                VALUES (?, ?, ?, ?)
                """,
                (999, "not-a-passage", "Search video", "needle"),
            )
        connection.commit()

    with pytest.raises(SearchIndexInvalid):
        index.status()


@pytest.mark.parametrize(
    ("name", "statement", "parameters"),
    [
        ("unsafe_source_path", "UPDATE documents SET source_relpath = ?", ("../escape.vtt",)),
        ("invalid_source_hash", "UPDATE documents SET source_sha256 = ?", ("not-a-sha256",)),
        ("noncanonical_document_identity", "UPDATE documents SET channel_id = ?", ("other-channel",)),
        ("invalid_passage_timestamp", "UPDATE passages SET start_seconds = ?", (-1.0,)),
        (
            "noncanonical_passage_url",
            "UPDATE passages SET youtube_url = ?",
            ("https://youtube.com/watch?v=VideoId_123&t=999s",),
        ),
        (
            "invalid_source_counter",
            "UPDATE index_meta SET value = ? WHERE key = 'sources_invalid'",
            ("0",),
        ),
    ],
)
def test_status_rejects_invalid_persisted_domain_records(
    tmp_path: Path,
    name: str,
    statement: str,
    parameters: tuple[object, ...],
) -> None:
    from yt_insights.search.sqlite_fts import SearchIndexInvalid, SQLiteFtsIndex

    database = tmp_path / f"{name}.sqlite3"
    index = SQLiteFtsIndex(database)
    index.rebuild(_manifest(text="needle"))
    with sqlite3.connect(database) as connection:
        connection.execute(statement, parameters)
        connection.commit()

    with pytest.raises(SearchIndexInvalid):
        index.status()


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
