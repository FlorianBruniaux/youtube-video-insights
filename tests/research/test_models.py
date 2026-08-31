from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
import math

import pytest

from yt_insights.research.models import (
    AcquisitionAttempt,
    CandidateStatus,
    CoverageMetrics,
    DatabaseSnapshot,
    DecisionRecord,
    EventRecord,
    FreshnessAssessment,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    RequiredUserAction,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchSession,
    ResearchState,
    SessionHistory,
    VideoEvidence,
    discovery_fingerprint,
    normalize_research_text,
)

VIDEO_ID = "abc123DEF45"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


def _query_session(*, topic: str = "Local AI inference", queries: tuple[QuerySpec, ...] | None = None) -> ResearchSession:
    selected_queries = (
        (QuerySpec("Local LLM inference cost"),) if queries is None else queries
    )
    return ResearchSession(
        session_id="01K4RESEARCH0000000000000000",
        topic=topic,
        queries=selected_queries,
        languages=("en",),
        freshness_profile=FreshnessProfile.STANDARD,
        discovery_fingerprint="a" * 64,
        state=ResearchState.ASSESSING,
        required_user_action=None,
        revision=0,
        retry_target=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_research_enums_expose_the_frozen_wire_values_and_freshness_days() -> None:
    assert [profile.value for profile in FreshnessProfile] == [
        "fast",
        "standard",
        "stable",
        "historical",
    ]
    assert [profile.maximum_age_days for profile in FreshnessProfile] == [14, 30, 90, None]
    assert [state.value for state in ResearchState] == [
        "assessing",
        "awaiting_sufficiency_confirmation",
        "discovering",
        "awaiting_candidate_approval",
        "acquiring",
        "reindexing",
        "completed",
        "failed_retryable",
        "cancelled",
    ]
    assert [action.value for action in RequiredUserAction] == [
        "confirm_sufficiency_or_refresh",
        "approve_candidates_or_cancel",
    ]
    assert [status.value for status in CandidateStatus] == [
        "candidate",
        "approved",
        "acquired",
        "already_present",
        "no_transcript",
        "failed_retryable",
    ]


@pytest.mark.parametrize("text", ["", " \t ", "query\x00text", "query\ntext"])
def test_query_spec_rejects_empty_or_control_character_text(text: str) -> None:
    with pytest.raises(ValueError):
        QuerySpec(text)


def test_query_spec_preserves_display_text_but_normalizes_for_identity() -> None:
    query = QuerySpec("  Local\u00a0LLM   Inference  ")

    assert query.text == "  Local\u00a0LLM   Inference  "
    assert normalize_research_text(query.text) == "local llm inference"


def test_session_accepts_one_to_eight_distinct_normalized_queries() -> None:
    session = _query_session(queries=tuple(QuerySpec(f"query {number}") for number in range(1, 9)))

    assert len(session.queries) == 8


@pytest.mark.parametrize(
    "queries",
    [(), tuple(QuerySpec(f"query {number}") for number in range(9))],
)
def test_session_rejects_query_counts_outside_the_contract(queries: tuple[QuerySpec, ...]) -> None:
    with pytest.raises(ValueError, match="between 1 and 8"):
        _query_session(queries=queries)


def test_session_rejects_normalized_duplicate_queries() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        _query_session(queries=(QuerySpec("Local AI"), QuerySpec(" local   ai ")))


@pytest.mark.parametrize("topic", ["x" * 501, "topic\x00", "topic\rtext"])
def test_session_rejects_long_or_control_character_topics(topic: str) -> None:
    with pytest.raises(ValueError):
        _query_session(topic=topic)


def test_query_spec_rejects_more_than_500_code_points_after_trimming() -> None:
    assert QuerySpec(" " + "x" * 500 + " ").text.endswith(" ")

    with pytest.raises(ValueError, match="500"):
        QuerySpec("x" * 501)


def test_video_evidence_parses_iso_dates_and_preserves_tuple_fields() -> None:
    evidence = VideoEvidence(
        query="Local AI",
        video_id=VIDEO_ID,
        source_keys=("corpus",),
        title="Local inference",
        published_at="2026-08-30",
        rank=1,
        watch_url=WATCH_URL,
    )

    assert evidence.published_at == date(2026, 8, 30)
    assert evidence.source_keys == ("corpus",)
    with pytest.raises(FrozenInstanceError):
        evidence.title = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("published_at", ["20260830", "2026-02-30", "2026-8-3"])
def test_video_evidence_rejects_noncanonical_dates(published_at: str) -> None:
    with pytest.raises(ValueError, match="date"):
        VideoEvidence(
            query="Local AI",
            video_id=VIDEO_ID,
            source_keys=("corpus",),
            title="Local inference",
            published_at=published_at,
            rank=1,
            watch_url=WATCH_URL,
        )


@pytest.mark.parametrize("video_id", ["short", "invalid/123", "abcdefghijkl"])
def test_research_candidate_rejects_invalid_video_ids(video_id: str) -> None:
    with pytest.raises(ValueError, match="video ID"):
        ResearchCandidate(
            video_id=video_id,
            title="Local inference",
            channel_id=None,
            channel_title=None,
            published_at=None,
            watch_url=WATCH_URL,
            matched_queries=("Local AI",),
            original_rank=1,
            status=CandidateStatus.CANDIDATE,
        )


def test_research_candidate_rejects_noncanonical_watch_urls() -> None:
    with pytest.raises(ValueError, match="watch URL"):
        ResearchCandidate(
            video_id=VIDEO_ID,
            title="Local inference",
            channel_id=None,
            channel_title=None,
            published_at=None,
            watch_url=f"https://youtu.be/{VIDEO_ID}",
            matched_queries=("Local AI",),
            original_rank=1,
            status=CandidateStatus.CANDIDATE,
        )


@pytest.mark.parametrize("rank", [math.inf, -math.inf, math.nan])
def test_evidence_rejects_nonfinite_ranks(rank: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        PassageEvidence(
            query="Local AI",
            passage_id="passage-1",
            video_id=VIDEO_ID,
            channel_id="channel-1",
            rank=rank,
            url=WATCH_URL,
            excerpt="excerpt",
            source_sha256="a" * 64,
        )


@pytest.mark.parametrize("excerpt", ["", " \t ", "x" * 1501])
def test_passage_evidence_rejects_blank_or_oversize_excerpts(excerpt: str) -> None:
    with pytest.raises(ValueError, match="excerpt"):
        PassageEvidence(
            query="Local AI",
            passage_id="passage-1",
            video_id=VIDEO_ID,
            channel_id="channel-1",
            rank=1,
            url=WATCH_URL,
            excerpt=excerpt,
            source_sha256="a" * 64,
        )


@pytest.mark.parametrize("source_sha256", ["a" * 63, "A" * 64, "g" * 64])
def test_passage_evidence_rejects_malformed_source_sha256(source_sha256: str) -> None:
    with pytest.raises(ValueError, match="source SHA-256"):
        PassageEvidence(
            query="Local AI",
            passage_id="passage-1",
            video_id=VIDEO_ID,
            channel_id="channel-1",
            rank=1,
            url=WATCH_URL,
            excerpt="excerpt",
            source_sha256=source_sha256,
        )


@pytest.mark.parametrize("source_sha256", ["a" * 63, "A" * 64, "g" * 64])
def test_acquisition_outcome_rejects_malformed_optional_source_sha256(
    source_sha256: str,
) -> None:
    with pytest.raises(ValueError, match="source SHA-256"):
        ResearchAcquisitionOutcome(
            "attempt",
            VIDEO_ID,
            CandidateStatus.ACQUIRED,
            None,
            source_sha256,
        )


@pytest.mark.parametrize(
    ("profile", "maximum_age_days"),
    [(FreshnessProfile.FAST, 30), (FreshnessProfile.HISTORICAL, 90)],
)
def test_freshness_assessment_rejects_mismatched_profile_maximum_age(
    profile: FreshnessProfile, maximum_age_days: int,
) -> None:
    with pytest.raises(ValueError, match="maximum age"):
        FreshnessAssessment(profile, maximum_age_days, NOW, True, "stale")


def test_tuple_fields_reject_mutable_sequences() -> None:
    with pytest.raises(TypeError, match="tuple"):
        VideoEvidence(
            query="Local AI",
            video_id=VIDEO_ID,
            source_keys=["corpus"],  # type: ignore[arg-type]
            title="Local inference",
            published_at=None,
            rank=1,
            watch_url=WATCH_URL,
        )


def test_all_shared_records_can_be_constructed_with_immutable_values() -> None:
    passage = PassageEvidence(
        query="Local AI",
        passage_id="passage-1",
        video_id=VIDEO_ID,
        channel_id="channel-1",
        rank=1,
        url=WATCH_URL,
        excerpt="excerpt",
        source_sha256="a" * 64,
    )
    video = VideoEvidence(
        query="Local AI",
        video_id=VIDEO_ID,
        source_keys=("corpus",),
        title="Local inference",
        published_at=date(2026, 8, 30),
        rank=1,
        watch_url=WATCH_URL,
    )
    coverage = CoverageMetrics(1, 1, 1, (), date(2026, 8, 30), 0)
    freshness = FreshnessAssessment(FreshnessProfile.FAST, 14, NOW, False, "fresh")
    assessment = ResearchAssessment(NOW, DatabaseSnapshot("search", "catalog"), coverage, freshness, (passage,), (video,))
    candidate = ResearchCandidate(
        VIDEO_ID,
        "Local inference",
        "channel-1",
        "Channel",
        date(2026, 8, 30),
        WATCH_URL,
        ("Local AI",),
        1,
        CandidateStatus.CANDIDATE,
    )
    decision = DecisionRecord("key", "refresh", "{}", NOW)
    attempt = AcquisitionAttempt("attempt", "key", "session", 1, "running", (VIDEO_ID,), NOW, NOW)
    outcome = ResearchAcquisitionOutcome("attempt", VIDEO_ID, CandidateStatus.ACQUIRED, None, "a" * 64)
    event = EventRecord(1, ResearchState.ASSESSING, ResearchState.DISCOVERING, "refresh", "{}", NOW)
    history = SessionHistory((assessment,), (decision,), (attempt,), (outcome,), (event,))

    assert candidate.matched_queries == ("Local AI",)
    assert history.events == (event,)


def test_discovery_fingerprint_is_deterministic_for_normalized_topic_text() -> None:
    queries = (
        QuerySpec("Local LLM inference cost"),
        QuerySpec("MLX versus Ollama performance"),
    )

    fingerprint = discovery_fingerprint(
        topic="Local AI inference",
        queries=queries,
        languages=("en",),
        provider_name="yt-dlp",
        provider_version=1,
    )

    assert len(fingerprint) == 64
    assert fingerprint == discovery_fingerprint(
        topic="  local   AI inference ",
        queries=queries,
        languages=("en",),
        provider_name="yt-dlp",
        provider_version=1,
    )
