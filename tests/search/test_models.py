"""Public-domain contract tests for the local search slice."""

from dataclasses import FrozenInstanceError, fields
from hashlib import sha256
import math

import pytest

from yt_insights.search.models import (
    BuildReport,
    DocumentRef,
    Passage,
    SearchHit,
    SearchQuery,
    compute_document_id,
    compute_passage_id,
    youtube_url,
)


CHANNEL_ID = "UC_DEMO"
VIDEO_ID = "dQw4w9WgXcQ"
LANGUAGE = "en"
SOURCE_SHA256 = "a" * 64
TEXT = "A useful transcript passage."


def expected_document_id(
    channel_id: str = CHANNEL_ID, video_id: str = VIDEO_ID, language: str = LANGUAGE
) -> str:
    return sha256(f"{channel_id}\0{video_id}\0{language}".encode("utf-8")).hexdigest()


def expected_passage_id(
    document_id: str | None = None,
    *,
    ordinal: int = 0,
    start_seconds: float = 12.5,
    end_seconds: float = 18.25,
    text: str = TEXT,
) -> str:
    normalized_text = " ".join(text.split())
    normalized_digest = sha256(normalized_text.encode("utf-8")).hexdigest()
    payload = "\0".join(
        (
            document_id or expected_document_id(),
            str(ordinal),
            str(round(start_seconds * 1000)),
            str(round(end_seconds * 1000)),
            normalized_digest,
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def make_document(**changes: object) -> DocumentRef:
    values: dict[str, object] = {
        "document_id": expected_document_id(),
        "source_relpath": "transcripts/demo.en.vtt",
        "source_sha256": SOURCE_SHA256,
        "channel_id": CHANNEL_ID,
        "channel_title": "Demo channel",
        "video_id": VIDEO_ID,
        "video_title": "Demo video",
        "language": LANGUAGE,
    }
    values.update(changes)
    if "document_id" not in changes:
        values["document_id"] = expected_document_id(
            str(values["channel_id"]), str(values["video_id"]), str(values["language"])
        )
    return DocumentRef(**values)  # type: ignore[arg-type]


def make_passage(**changes: object) -> Passage:
    values: dict[str, object] = {
        "passage_id": expected_passage_id(),
        "document_id": expected_document_id(),
        "ordinal": 0,
        "start_seconds": 12.5,
        "end_seconds": 18.25,
        "text": TEXT,
        "youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ&t=12s",
    }
    values.update(changes)
    if (
        "passage_id" not in changes
        and math.isfinite(float(values["start_seconds"]))
        and math.isfinite(float(values["end_seconds"]))
    ):
        values["passage_id"] = expected_passage_id(
            str(values["document_id"]),
            ordinal=int(values["ordinal"]),
            start_seconds=float(values["start_seconds"]),
            end_seconds=float(values["end_seconds"]),
            text=str(values["text"]),
        )
    return Passage(**values)  # type: ignore[arg-type]


def test_records_have_exact_slotted_immutable_public_fields() -> None:
    assert tuple(field.name for field in fields(DocumentRef)) == (
        "document_id",
        "source_relpath",
        "source_sha256",
        "channel_id",
        "channel_title",
        "video_id",
        "video_title",
        "language",
    )
    assert tuple(field.name for field in fields(Passage)) == (
        "passage_id",
        "document_id",
        "ordinal",
        "start_seconds",
        "end_seconds",
        "text",
        "youtube_url",
    )
    assert tuple(field.name for field in fields(SearchQuery)) == ("text", "channel", "language", "limit")
    assert tuple(field.name for field in fields(SearchHit)) == ("document", "passage", "rank", "score")
    assert tuple(field.name for field in fields(BuildReport)) == (
        "sources_discovered",
        "sources_selected",
        "sources_invalid",
        "documents_indexed",
        "passages_indexed",
        "invalid_sources",
    )

    document = make_document()
    assert not hasattr(document, "__dict__")
    with pytest.raises(FrozenInstanceError):
        document.language = "fr"  # type: ignore[misc]


def test_document_id_helper_uses_utf8_nul_separated_identity() -> None:
    assert compute_document_id(CHANNEL_ID, VIDEO_ID, LANGUAGE) == expected_document_id()


@pytest.mark.parametrize(
    ("channel_id", "language"),
    (("channel\0identity", LANGUAGE), (CHANNEL_ID, "en\0identity")),
)
def test_document_identity_rejects_nul_delimited_components(channel_id: str, language: str) -> None:
    with pytest.raises(ValueError, match="channel_id|language"):
        compute_document_id(channel_id, VIDEO_ID, language)
    with pytest.raises(ValueError, match="channel_id|language"):
        make_document(channel_id=channel_id, language=language)


def test_both_nul_collision_tuples_cannot_construct_documents() -> None:
    colliding_id = expected_document_id("alpha", VIDEO_ID, f"{VIDEO_ID}\0en")
    assert colliding_id == expected_document_id(f"alpha\0{VIDEO_ID}", VIDEO_ID, "en")

    for channel_id, language in (
        ("alpha", f"{VIDEO_ID}\0en"),
        (f"alpha\0{VIDEO_ID}", "en"),
    ):
        with pytest.raises(ValueError, match="channel_id|language"):
            make_document(
                document_id=colliding_id,
                channel_id=channel_id,
                language=language,
            )


def test_document_rejects_an_id_not_derived_from_its_identity() -> None:
    with pytest.raises(ValueError, match="document_id"):
        make_document(document_id="b" * 64)


@pytest.mark.parametrize(
    "source_relpath",
    ("", "/absolute/file.vtt", "transcripts/../file.vtt", "transcripts\\file.vtt"),
)
def test_document_rejects_invalid_source_relative_paths(source_relpath: str) -> None:
    with pytest.raises(ValueError, match="source_relpath"):
        make_document(source_relpath=source_relpath)


@pytest.mark.parametrize("video_id", ("too-short", "dQw4w9WgXc!"))
def test_document_rejects_non_youtube_video_ids(video_id: str) -> None:
    with pytest.raises(ValueError, match="video_id"):
        make_document(video_id=video_id)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("source_sha256", "A" * 64),
        ("source_sha256", "a" * 63),
        ("channel_id", "  "),
        ("channel_title", "  "),
        ("video_title", "  "),
        ("language", "  "),
    ),
)
def test_document_rejects_invalid_hashes_and_blank_metadata(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        make_document(**{field: value})


def test_passage_id_helper_normalizes_text_and_millisecond_bounds() -> None:
    assert compute_passage_id(
        expected_document_id(), 0, 12.5, 18.25, "  A useful\n transcript   passage.  "
    ) == expected_passage_id()


def test_passage_rejects_an_id_not_derived_from_its_content() -> None:
    with pytest.raises(ValueError, match="passage_id"):
        make_passage(passage_id="b" * 64)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("ordinal", -1),
        ("start_seconds", -0.1),
        ("start_seconds", math.inf),
        ("end_seconds", math.nan),
        ("text", " \t\n "),
    ),
)
def test_passage_rejects_invalid_ordinal_timestamps_and_source_text(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        make_passage(**{field: value})


def test_passage_rejects_an_end_before_its_start() -> None:
    with pytest.raises(ValueError, match="end_seconds"):
        make_passage(end_seconds=12.49)


def test_youtube_url_helper_matches_existing_timestamp_url_format() -> None:
    assert youtube_url(VIDEO_ID, 12.9) == "https://youtube.com/watch?v=dQw4w9WgXcQ&t=12s"


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com/watch?v=dQw4w9WgXcQ&t=12s",
        "https://youtube.com/watch?v=dQw4w9WgXcQ&t=13s",
    ),
)
def test_passage_rejects_noncanonical_youtube_urls(url: str) -> None:
    with pytest.raises(ValueError, match="youtube_url"):
        make_passage(youtube_url=url)


def test_search_query_defaults_and_rejects_invalid_inputs() -> None:
    assert SearchQuery("observability") == SearchQuery("observability", limit=10)

    with pytest.raises(ValueError, match="text"):
        SearchQuery(" \n")
    with pytest.raises(ValueError, match="channel"):
        SearchQuery("observability", channel="  ")
    with pytest.raises(ValueError, match="language"):
        SearchQuery("observability", language="\t")
    for limit in (0, 21):
        with pytest.raises(ValueError, match="limit"):
            SearchQuery("observability", limit=limit)


def test_search_hit_requires_matching_document_rank_and_finite_score() -> None:
    document = make_document()
    passage = make_passage()
    assert SearchHit(document=document, passage=passage, rank=1, score=0.8).rank == 1

    with pytest.raises(ValueError, match="document_id"):
        SearchHit(document=document, passage=make_passage(document_id="c" * 64), rank=1, score=0.8)
    with pytest.raises(ValueError, match="youtube_url"):
        SearchHit(
            document=document,
            passage=make_passage(youtube_url=youtube_url("abcdefghijk", 12.5)),
            rank=1,
            score=0.8,
        )
    with pytest.raises(ValueError, match="rank"):
        SearchHit(document=document, passage=passage, rank=0, score=0.8)
    with pytest.raises(ValueError, match="score"):
        SearchHit(document=document, passage=passage, rank=1, score=math.inf)


def test_build_report_defaults_and_rejects_inconsistent_counters() -> None:
    report = BuildReport(2, 1, 1, 1, 3)
    assert report.invalid_sources == ()

    with pytest.raises(ValueError, match="sources_discovered"):
        BuildReport(-1, 0, 0, 0, 0)
    with pytest.raises(ValueError, match="sources_selected"):
        BuildReport(1, 2, 0, 0, 0)
    with pytest.raises(ValueError, match="sources_invalid"):
        BuildReport(1, 1, 1, 0, 0)
