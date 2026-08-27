from __future__ import annotations

import pytest

from yt_insights.search.models import DocumentRef, compute_document_id


def _document() -> DocumentRef:
    return DocumentRef(
        document_id=compute_document_id("bold-guy", "AbCdEf12345", "fr"),
        source_relpath="bold-guy/transcripts/Video [AbCdEf12345].fr.vtt",
        source_sha256="a" * 64,
        channel_id="bold-guy",
        channel_title="bold-guy",
        video_id="AbCdEf12345",
        video_title="Video",
        language="fr",
    )


def test_build_passages_emits_one_short_transcript_with_timestamp_end() -> None:
    from yt_insights.search.chunker import build_passages

    passages = build_passages(
        _document(),
        [
            {"start": 0.0, "text": "  premier   segment  "},
            {"start": 10.0, "text": "second segment"},
        ],
    )

    assert len(passages) == 1
    passage = passages[0]
    assert (passage.ordinal, passage.start_seconds, passage.end_seconds) == (0, 0.0, 10.0)
    assert passage.text == "premier segment second segment"
    assert passage.youtube_url == "https://youtube.com/watch?v=AbCdEf12345&t=0s"


def test_build_passages_targets_both_word_and_duration_minima_with_overlap() -> None:
    from yt_insights.search.chunker import build_passages

    segments = [
        {"start": float(index * 10), "text": f"s{index} " + "word " * 24}
        for index in range(8)
    ]

    passages = build_passages(_document(), segments)

    assert [(item.ordinal, item.start_seconds, item.end_seconds) for item in passages] == [
        (0, 0.0, 60.0),
        (1, 50.0, 70.0),
    ]
    assert passages[1].text.startswith("s5")


def test_build_passages_emits_an_oversized_single_segment_alone() -> None:
    from yt_insights.search.chunker import build_passages

    passages = build_passages(
        _document(),
        [
            {"start": 0.0, "text": "large " * 221},
            {"start": 10.0, "text": "tail"},
        ],
    )

    assert [(item.start_seconds, item.end_seconds) for item in passages] == [(0.0, 10.0), (10.0, 10.0)]
    assert len(passages[0].text.split()) == 221


@pytest.mark.parametrize(
    "segments",
    [
        [{"start": -1, "text": "text"}],
        [{"start": float("inf"), "text": "text"}],
        [{"start": 0, "text": "   "}],
        [{"start": "0", "text": "text"}],
    ],
)
def test_build_passages_rejects_invalid_segment_shapes(segments: list[dict]) -> None:
    from yt_insights.search.chunker import build_passages

    with pytest.raises(ValueError, match="segment"):
        build_passages(_document(), segments)
