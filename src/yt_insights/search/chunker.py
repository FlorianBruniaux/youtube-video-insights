"""Deterministic timestamp-aware transcript passage construction."""

from __future__ import annotations

from math import isfinite

from .models import DocumentRef, Passage, compute_passage_id, youtube_url


MIN_WORDS = 100
MAX_WORDS = 220
MIN_DURATION_SECONDS = 45
MAX_DURATION_SECONDS = 90
OVERLAP_SECONDS = 12


def _normalize_segments(segments: list[dict]) -> list[tuple[float, str]]:
    normalized: list[tuple[float, str]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("segment must be a dictionary")
        start = segment.get("start")
        text = segment.get("text")
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, float))
            or not isfinite(start)
            or start < 0
        ):
            raise ValueError("segment start must be finite and non-negative")
        if not isinstance(text, str) or not (normalized_text := " ".join(text.split())):
            raise ValueError("segment text must be non-empty")
        normalized.append((float(start), normalized_text))
    return sorted(normalized, key=lambda segment: segment[0])


def build_passages(document: DocumentRef, segments: list[dict]) -> tuple[Passage, ...]:
    """Build overlapping, timestamp-aligned passages for one document."""
    source_segments = _normalize_segments(segments)
    if not source_segments:
        return ()

    passages: list[Passage] = []
    start_index = 0
    total_segments = len(source_segments)

    while start_index < total_segments:
        chunk_start, first_text = source_segments[start_index]
        texts = [first_text]
        word_count = len(first_text.split())
        final_index = start_index

        if word_count <= MAX_WORDS:
            while final_index + 1 < total_segments:
                next_start, next_text = source_segments[final_index + 1]
                next_words = word_count + len(next_text.split())
                next_duration = next_start - chunk_start
                if next_words > MAX_WORDS or next_duration > MAX_DURATION_SECONDS:
                    break
                final_index += 1
                texts.append(next_text)
                word_count = next_words
                if word_count >= MIN_WORDS and next_duration >= MIN_DURATION_SECONDS:
                    break

        end_seconds = (
            source_segments[final_index + 1][0]
            if final_index + 1 < total_segments
            else source_segments[final_index][0]
        )
        text = " ".join(texts)
        ordinal = len(passages)
        passages.append(
            Passage(
                passage_id=compute_passage_id(
                    document.document_id, ordinal, chunk_start, end_seconds, text
                ),
                document_id=document.document_id,
                ordinal=ordinal,
                start_seconds=chunk_start,
                end_seconds=end_seconds,
                text=text,
                youtube_url=youtube_url(document.video_id, chunk_start),
            )
        )
        if final_index == total_segments - 1:
            break
        threshold = end_seconds - OVERLAP_SECONDS
        start_index = next(
            index
            for index in range(start_index + 1, total_segments)
            if source_segments[index][0] >= threshold
        )

    return tuple(passages)
