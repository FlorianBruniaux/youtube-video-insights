from __future__ import annotations

import pytest

from yt_insights.downloader import VideoInfo, VideoListResult
from yt_insights.research.discovery import YtDlpDiscoveryProvider
from yt_insights.research.models import QuerySpec


def _video(
    video_id: str,
    *,
    channel_id: str = "",
    channel_title: str = "",
) -> VideoInfo:
    return VideoInfo(
        video_id=video_id,
        title=f"Title {video_id}",
        upload_date="20260828",
        channel_id=channel_id,
        channel_title=channel_title,
    )


def test_discovery_builds_bounded_sources_deduplicates_and_diversifies_channels() -> None:
    queries = (QuerySpec("first topic"), QuerySpec("second topic"))
    calls: list[str] = []
    known_calls: list[tuple[str, ...]] = []
    by_source = {
        "ytsearch10:first topic": VideoListResult(
            videos=[
                _video("aaaaaaaaaaa", channel_id="channel-a"),
                _video("bbbbbbbbbbb", channel_id="channel-a"),
                _video("ccccccccccc", channel_id="channel-a"),
                _video("eeeeeeeeeee"),
            ]
        ),
        "ytsearch10:second topic": VideoListResult(
            videos=[
                _video("bbbbbbbbbbb", channel_id="not-the-source-slug"),
                _video("ddddddddddd", channel_id="channel-b"),
            ],
            errors=["external stderr must not be retained"],
            returncode=1,
        ),
    }

    def fetcher(source: str) -> VideoListResult:
        calls.append(source)
        return by_source[source]

    def existing_ids(video_ids: tuple[str, ...]) -> frozenset[str]:
        known_calls.append(video_ids)
        return frozenset({"aaaaaaaaaaa"})

    result = YtDlpDiscoveryProvider(
        fetcher=fetcher, existing_ids=existing_ids
    ).discover(queries)

    assert calls == ["ytsearch10:first topic", "ytsearch10:second topic"]
    assert len(calls) <= 8
    assert known_calls == [
        ("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "eeeeeeeeeee", "ddddddddddd")
    ]
    assert [candidate.video_id for candidate in result.candidates] == [
        "bbbbbbbbbbb",
        "ddddddddddd",
        "eeeeeeeeeee",
        "ccccccccccc",
    ]
    assert [candidate.original_rank for candidate in result.candidates] == [2, 2, 4, 3]
    assert result.candidates[0].matched_queries == ("first topic", "second topic")
    assert result.candidates[0].channel_id == "channel-a"
    assert result.candidates[2].channel_id is None
    assert len(result.candidates) <= 10
    assert result.errors == ("provider_exit_nonzero", "partial_metadata")
    assert result.completed is False


def test_discovery_never_calls_more_than_eight_queries_or_returns_more_than_limit() -> None:
    queries = tuple(QuerySpec(f"topic {index}") for index in range(8))
    calls: list[str] = []

    def fetcher(source: str) -> VideoListResult:
        calls.append(source)
        index = int(source.rsplit(" ", 1)[1])
        return VideoListResult(
            videos=[
                _video(f"{index:010d}a"),
                _video(f"{index:010d}b"),
            ]
        )

    result = YtDlpDiscoveryProvider(
        fetcher=fetcher, existing_ids=lambda _: frozenset()
    ).discover(queries, limit=10)

    assert calls == [f"ytsearch10:topic {index}" for index in range(8)]
    assert len(calls) == 8
    assert len(result.candidates) == 10
    assert result.completed is True


def test_discovery_reads_at_most_ten_metadata_records_per_query() -> None:
    videos = [_video(f"{index:010d}x") for index in range(11)]
    known_calls: list[tuple[str, ...]] = []

    result = YtDlpDiscoveryProvider(
        fetcher=lambda _: VideoListResult(videos=videos),
        existing_ids=lambda video_ids: known_calls.append(video_ids) or frozenset(),
    ).discover((QuerySpec("bounded records"),))

    assert known_calls == [tuple(video.video_id for video in videos[:10])]
    assert [candidate.video_id for candidate in result.candidates] == [
        video.video_id for video in videos[:10]
    ]


def test_discovery_records_bounded_errors_and_refuses_an_empty_success() -> None:
    def fetcher(source: str) -> VideoListResult:
        assert source == "ytsearch10:valid topic"
        return VideoListResult(videos=[_video("not-valid-id")])

    result = YtDlpDiscoveryProvider(
        fetcher=fetcher, existing_ids=lambda _: frozenset()
    ).discover((QuerySpec("valid topic"),))

    assert result.candidates == ()
    assert result.errors == ("invalid_metadata_record", "no_candidates")
    assert result.completed is False


def test_discovery_rejects_noncanonical_unicode_video_ids() -> None:
    result = YtDlpDiscoveryProvider(
        fetcher=lambda _: VideoListResult(videos=[_video("é" * 11)]),
        existing_ids=lambda _: frozenset(),
    ).discover((QuerySpec("valid topic"),))

    assert result.candidates == ()
    assert result.errors == ("invalid_metadata_record", "no_candidates")


@pytest.mark.parametrize("text", ["ytsearch10:injected", "query\x00text", "x" * 501])
def test_discovery_revalidates_untrusted_query_text(text: str) -> None:
    query = object.__new__(QuerySpec)
    object.__setattr__(query, "text", text)
    provider = YtDlpDiscoveryProvider(
        fetcher=lambda _: pytest.fail("fetcher must not run"),
        existing_ids=lambda _: pytest.fail("catalogue must not run"),
    )

    with pytest.raises(ValueError):
        provider.discover((query,))
