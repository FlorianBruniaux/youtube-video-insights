from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights.acquisition import (
    AcquisitionItemReport,
    AcquisitionItemStatus,
    AcquisitionPlan,
    AcquisitionReport,
    SourceKind,
)
from yt_insights.downloader import VideoInfo, VideoListResult
from yt_insights.paths import DataPaths
from yt_insights.research.models import FreshnessProfile
from yt_insights.web.api import (
    PlanChanged,
    RequestValidationError,
    SourceAcquisitionFacade,
    parse_acquisition,
    parse_approval,
    parse_decision,
    parse_export,
    parse_pagination,
    parse_search,
    parse_source_acquisition,
    parse_source_preview,
    parse_start_session,
    validate_session_id,
)
from yt_insights.web.models import WebRequest, WebResponse


def _json_body(payload: object) -> bytes:
    return json.dumps(payload).encode("utf-8")


def test_web_request_get_preserves_repeated_query_values() -> None:
    request = WebRequest.get("/api/v1/search", "q=local&q=remote&limit=10")

    assert request.method == "GET"
    assert request.path == "/api/v1/search"
    assert request.query == {"q": ("local", "remote"), "limit": ("10",)}


def test_web_response_json_emits_a_stable_json_object() -> None:
    response = WebResponse.json(200, {"schema_version": 1, "status": "ok"})

    assert response.status == 200
    assert response.content_type == "application/json; charset=utf-8"
    assert response.json_body == {"schema_version": 1, "status": "ok"}


@pytest.mark.parametrize(
    "query",
    [
        {"q": ("local", "remote")},
        {"q": ("local",), "surprise": ("value",)},
        {"q": ("local\x00remote",)},
        {"q": ("x" * 501,)},
        {"q": ("local",), "limit": ("True",)},
    ],
)
def test_parse_search_rejects_ambiguous_or_unknown_query_values(
    query: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(RequestValidationError):
        parse_search(query)


def test_parse_search_strips_and_builds_the_domain_query() -> None:
    parsed = parse_search(
        {
            "q": ("  local agents  ",),
            "channel": ("  channel-id ",),
            "language": (" fr ",),
            "limit": ("10",),
        }
    )

    assert parsed.text == "local agents"
    assert parsed.channel == "channel-id"
    assert parsed.language == "fr"
    assert parsed.limit == 10


@pytest.mark.parametrize(
    "query",
    [
        {"limit": ("10", "20")},
        {"limit": ("true",)},
        {"offset": ("-1",)},
        {"cursor": ("1",)},
    ],
)
def test_parse_pagination_is_closed_world(
    query: dict[str, tuple[str, ...]],
) -> None:
    with pytest.raises(RequestValidationError):
        parse_pagination(query)


def test_public_integers_are_bounded_to_sqlite_signed_64_bit() -> None:
    with pytest.raises(RequestValidationError):
        parse_pagination({"offset": (str(2**63),)})
    with pytest.raises(RequestValidationError):
        parse_decision(
            _json_body(
                {
                    "expected_revision": 2**63,
                    "decision": "refresh",
                    "idempotency_key": "decision-1",
                }
            )
        )


def test_public_integer_text_is_rejected_before_unbounded_conversion() -> None:
    oversized_integer = "9" * 5_000

    with pytest.raises(RequestValidationError):
        parse_pagination({"offset": (oversized_integer,)})
    with pytest.raises(RequestValidationError):
        parse_decision(
            (
                '{"expected_revision":'
                + oversized_integer
                + ',"decision":"refresh","idempotency_key":"decision-1"}'
            ).encode("ascii")
        )


def test_parse_start_session_rejects_unknown_fields_and_boolean_integers() -> None:
    with pytest.raises(RequestValidationError):
        parse_start_session(
            _json_body(
                {
                    "topic": "local agents",
                    "queries": ["local agents"],
                    "languages": ["fr"],
                    "freshness_profile": "standard",
                    "idempotency_key": "start-1",
                    "revision": True,
                }
            )
        )


def test_session_creation_requires_an_idempotency_key() -> None:
    with pytest.raises(RequestValidationError):
        parse_start_session(
            _json_body(
                {
                    "topic": "local agents",
                    "queries": ["local agents"],
                    "languages": ["fr"],
                    "freshness_profile": "standard",
                }
            )
        )


def test_parse_start_session_rejects_duplicate_json_keys() -> None:
    with pytest.raises(RequestValidationError):
        parse_start_session(
            b'{"topic":"first","topic":"second","queries":["q"],'
            b'"languages":[],"freshness_profile":"standard",'
            b'"idempotency_key":"start-1"}'
        )


def test_parse_start_session_returns_stripped_typed_values() -> None:
    parsed = parse_start_session(
        _json_body(
            {
                "topic": "  local agents ",
                "queries": [" evidence "],
                "languages": [" fr "],
                "freshness_profile": "standard",
                "idempotency_key": " start-1 ",
            }
        )
    )

    assert parsed.topic == "local agents"
    assert parsed.queries == ("evidence",)
    assert parsed.languages == ("fr",)
    assert parsed.freshness_profile is FreshnessProfile.STANDARD
    assert parsed.idempotency_key == "start-1"


@pytest.mark.parametrize(
    "payload",
    [
        {
            "topic": "valid\u0001topic",
            "queries": ["local AI"],
            "languages": ["fr"],
            "freshness_profile": "standard",
            "idempotency_key": "start-1",
        },
        {
            "topic": "Local AI",
            "queries": ["AI  code", "ai code"],
            "languages": ["fr"],
            "freshness_profile": "standard",
            "idempotency_key": "start-1",
        },
    ],
)
def test_parse_start_session_rejects_domain_invalid_text_before_workflow(
    payload: dict[str, object],
) -> None:
    """Parser/domain drift would turn invalid public input into an HTTP 500."""
    with pytest.raises(RequestValidationError):
        parse_start_session(_json_body(payload))


def test_parse_decision_rejects_boolean_revision() -> None:
    with pytest.raises(RequestValidationError):
        parse_decision(
            _json_body(
                {
                    "expected_revision": True,
                    "decision": "refresh",
                    "idempotency_key": "decision-1",
                }
            )
        )


def test_parse_decision_rejects_non_ascii_idempotency_key() -> None:
    with pytest.raises(RequestValidationError):
        parse_decision(
            _json_body(
                {
                    "expected_revision": 4,
                    "decision": "refresh",
                    "idempotency_key": "décision-1",
                }
            )
        )


def test_parse_approval_deduplicates_and_bounds_video_ids() -> None:
    parsed = parse_approval(
        _json_body(
            {
                "expected_revision": 4,
                "video_ids": ["abc123DEF45", "abc123DEF45", "zyx987WVUT0"],
                "idempotency_key": "approval-1",
            }
        )
    )
    assert parsed.video_ids == ("abc123DEF45", "zyx987WVUT0")

    with pytest.raises(RequestValidationError):
        parse_approval(
            _json_body(
                {
                    "expected_revision": 4,
                    "video_ids": [
                        "abc123DEF45",
                        "abc123DEF46",
                        "abc123DEF47",
                        "abc123DEF48",
                        "abc123DEF49",
                        "abc123DEF40",
                    ],
                    "idempotency_key": "approval-2",
                }
            )
        )


@pytest.mark.parametrize("session_id", ["", "../secret", "a/b", "x" * 129])
def test_validate_session_id_rejects_unsafe_identifiers(session_id: str) -> None:
    with pytest.raises(RequestValidationError):
        validate_session_id(session_id)


def test_parse_acquisition_rejects_cookie_selectors_and_unknown_fields() -> None:
    with pytest.raises(RequestValidationError):
        parse_acquisition(
            _json_body(
                {
                    "expected_revision": 5,
                    "idempotency_key": "acquisition-1",
                    "language": "fr",
                    "cookies_from_browser": "chrome",
                }
            )
        )


def test_parse_export_requires_a_real_boolean() -> None:
    assert parse_export(_json_body({"force": False})).force is False
    with pytest.raises(RequestValidationError):
        parse_export(_json_body({"force": 0}))


def test_source_request_parsers_are_closed_world_and_bind_the_fingerprint() -> None:
    preview = parse_source_preview(
        _json_body(
            {
                "source": " https://www.youtube.com/@example ",
                "slug": " example ",
                "years": [2025, 2026],
                "language": " fr ",
                "analyze": False,
            }
        )
    )
    assert preview.source == "https://www.youtube.com/@example"
    assert preview.slug == "example"
    assert preview.years == frozenset({2025, 2026})

    confirmed = parse_source_acquisition(
        _json_body(
            {
                "fingerprint": "a" * 64,
                "idempotency_key": "source-acquisition-1",
            }
        )
    )
    assert confirmed.fingerprint == "a" * 64
    assert confirmed.idempotency_key == "source-acquisition-1"

    with pytest.raises(RequestValidationError):
        parse_source_acquisition(
            _json_body(
                {
                    "source": "https://www.youtube.com/@example",
                    "fingerprint": "a" * 64,
                    "idempotency_key": "source-acquisition-1",
                }
            )
        )

    with pytest.raises(RequestValidationError):
        parse_source_preview(
            _json_body(
                {
                    "source": "https://www.youtube.com/@example",
                    "years": [True],
                }
            )
        )


def _source_plan(
    data_paths: DataPaths, *, video_id: str = "abc123DEF45"
) -> AcquisitionPlan:
    video = VideoInfo(
        video_id,
        "Safe title",
        "20260830",
        "channel-id",
        "Channel title",
    )
    return AcquisitionPlan(
        source="https://www.youtube.com/@example",
        source_kind=SourceKind.CHANNEL,
        output_root=data_paths.root / "private-channel",
        transcripts_dir=data_paths.root / "private-channel" / "transcripts",
        insights_dir=data_paths.root / "private-channel" / "insights",
        data_paths=data_paths,
        selected_videos=(video,),
        selected_urls=(video.watch_url,),
        selected_count=1,
        language="fr",
        analyze=False,
        requires_confirmation=True,
        exclusions=("private exclusion detail",),
        discovery_errors=("failed at /Users/private/cookies.txt",),
    )


def test_source_facade_calls_domain_services_and_returns_a_path_free_plan(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "private-data")
    plan = _source_plan(paths)
    calls: list[str] = []

    def classify(source: str) -> SourceKind:
        calls.append(f"classify:{source}")
        return SourceKind.CHANNEL

    def fetch(source: str) -> VideoListResult:
        calls.append(f"fetch:{source}")
        return VideoListResult(videos=list(plan.selected_videos))

    def build(**kwargs: object) -> AcquisitionPlan:
        calls.append(f"build:{kwargs['source']}")
        return plan

    facade = SourceAcquisitionFacade(
        paths,
        classify=classify,
        fetch=fetch,
        build=build,
        execute=lambda value: AcquisitionReport(0, 0, 0, ()),
    )
    request = parse_source_preview(
        _json_body(
            {
                "source": "https://www.youtube.com/@example",
                "language": "fr",
                "analyze": False,
            }
        )
    )

    payload = facade.preview(request)

    assert calls == [
        "classify:https://www.youtube.com/@example",
        "fetch:https://www.youtube.com/@example",
        "build:https://www.youtube.com/@example",
    ]
    assert payload["fingerprint"]
    assert payload["discovery_error_count"] == 1
    encoded = json.dumps(payload)
    assert str(paths.root) not in encoded
    assert "/Users/private/cookies.txt" not in encoded
    assert (
        not {"source", "output_root", "transcripts_dir", "insights_dir"}
        & payload.keys()
    )


def test_source_facade_bounds_the_video_sample_without_losing_the_fingerprint(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "private-data")
    videos = tuple(
        VideoInfo(
            f"vid{i:08d}"[-11:],
            "🎬" * 300,
            "20260830",
            "channel-id",
            "Channel title",
        )
        for i in range(200)
    )
    plan = AcquisitionPlan(
        source="https://www.youtube.com/@example",
        source_kind=SourceKind.CHANNEL,
        output_root=paths.root / "private-channel",
        transcripts_dir=paths.root / "private-channel" / "transcripts",
        insights_dir=paths.root / "private-channel" / "insights",
        data_paths=paths,
        selected_videos=videos,
        selected_urls=tuple(video.watch_url for video in videos),
        selected_count=len(videos),
        language="fr",
        analyze=False,
        requires_confirmation=True,
    )
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(videos=list(videos)),
        build=lambda **kwargs: plan,
        execute=lambda value: AcquisitionReport(0, 0, 0, ()),
    )
    request = parse_source_preview(
        _json_body({"source": plan.source, "language": "fr", "analyze": False})
    )

    payload = facade.preview(request)

    assert payload["fingerprint"]
    assert payload["videos_truncated"] is True
    assert payload["video_ids"] == [video.video_id for video in videos]
    assert (
        len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        < 20 * 1024
    )


def test_source_facade_rejects_more_than_one_thousand_selected_videos(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "private-data")
    videos = tuple(
        VideoInfo(
            f"vid{i:08d}",
            "Safe title",
            "20260830",
            "channel-id",
            "Channel title",
        )
        for i in range(1_001)
    )
    base = _source_plan(paths)
    plan = replace(
        base,
        selected_videos=videos,
        selected_urls=tuple(video.watch_url for video in videos),
        selected_count=len(videos),
    )
    calls: list[str] = []
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: calls.append("classify") or SourceKind.CHANNEL,
        fetch=lambda source: calls.append("fetch") or VideoListResult(),
        build=lambda **kwargs: calls.append("build") or plan,
        execute=lambda value: calls.append("execute") or AcquisitionReport(0, 0, 0, ()),
    )
    request = parse_source_preview(
        _json_body({"source": plan.source, "language": "fr", "analyze": False})
    )

    result = facade.preview(request)

    assert result == {"schema_version": 1, "error": {"code": "plan_too_large"}}
    assert calls == ["classify", "fetch", "build"]
    with pytest.raises(PlanChanged):
        facade.prepare_acquisition("a" * 64)
    assert calls == ["classify", "fetch", "build"]


def test_source_facade_rejects_a_plan_with_unpaired_acquisition_urls(
    tmp_path: Path,
) -> None:
    """A hidden URL without a displayed video ID would bypass exact-ID approval."""
    paths = DataPaths.from_root(tmp_path / "private-data")
    base = _source_plan(paths)
    unsafe = replace(base, selected_urls=())
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(),
        build=lambda **kwargs: unsafe,
    )

    payload = facade.preview(
        parse_source_preview(
            _json_body({"source": base.source, "language": "fr", "analyze": False})
        )
    )

    assert payload == {"schema_version": 1, "error": {"code": "plan_too_large"}}
    with pytest.raises(PlanChanged):
        facade.prepare_acquisition("a" * 64)


def test_source_facade_rejects_an_oversized_canonical_plan_before_caching(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "private-data")
    base = _source_plan(paths)
    oversized_video = replace(base.selected_videos[0], title="x" * 524_288)
    plan = replace(
        base,
        selected_videos=(oversized_video,),
        selected_urls=(oversized_video.watch_url,),
    )
    executed: list[AcquisitionPlan] = []
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(),
        build=lambda **kwargs: plan,
        execute=lambda value: executed.append(value) or AcquisitionReport(0, 0, 0, ()),
    )

    result = facade.preview(
        parse_source_preview(
            _json_body({"source": plan.source, "language": "fr", "analyze": False})
        )
    )

    assert result == {"schema_version": 1, "error": {"code": "plan_too_large"}}
    assert str(paths.root) not in json.dumps(result)
    assert executed == []


def test_source_facade_evicts_oldest_plans_to_stay_within_total_byte_budget(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "private-data")
    base = _source_plan(paths)
    sources = (
        "https://www.youtube.com/@first",
        "https://www.youtube.com/@second",
    )
    plans = {
        source: replace(
            base,
            source=source,
            selected_videos=(
                replace(
                    base.selected_videos[0],
                    video_id=f"vid{index:08d}",
                    title=label * 3_000,
                ),
            ),
        )
        for index, (source, label) in enumerate(zip(sources, ("a", "b"), strict=True))
    }
    plans = {
        source: replace(
            plan,
            selected_urls=(plan.selected_videos[0].watch_url,),
        )
        for source, plan in plans.items()
    }
    executed: list[str] = []
    provider_calls: list[str] = []
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: (
            provider_calls.append(f"classify:{source}") or SourceKind.CHANNEL
        ),
        fetch=lambda source: (
            provider_calls.append(f"fetch:{source}") or VideoListResult()
        ),
        build=lambda **kwargs: (
            provider_calls.append(f"build:{kwargs['source']}")
            or plans[str(kwargs["source"])]
        ),
        execute=lambda value: (
            executed.append(value.source) or AcquisitionReport(0, 0, 0, ())
        ),
        max_plan_identity_bytes=10_000,
        max_retained_identity_bytes=8_000,
    )

    fingerprints = [
        str(
            facade.preview(
                parse_source_preview(
                    _json_body({"source": source, "language": "fr", "analyze": False})
                )
            )["fingerprint"]
        )
        for source in sources
    ]

    provider_calls_before_missing_confirmation = list(provider_calls)
    with pytest.raises(PlanChanged):
        facade.prepare_acquisition(fingerprints[0])
    assert provider_calls == provider_calls_before_missing_confirmation
    assert executed == []
    facade.prepare_acquisition(fingerprints[1])()
    replayed = facade.preview(
        parse_source_preview(
            _json_body({"source": sources[0], "language": "fr", "analyze": False})
        )
    )
    assert replayed["fingerprint"] == fingerprints[0]
    facade.prepare_acquisition(fingerprints[0])()
    with pytest.raises(PlanChanged):
        facade.prepare_acquisition(fingerprints[1])
    assert executed == [sources[1], sources[0]]


def test_source_facade_executes_only_the_plan_matching_the_preview_fingerprint(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "data")
    stable_plan = _source_plan(paths)
    executed: list[AcquisitionPlan] = []

    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(videos=list(stable_plan.selected_videos)),
        build=lambda **kwargs: stable_plan,
        execute=lambda plan: (
            executed.append(plan)
            or AcquisitionReport(
                selected=1,
                transcripts_ready=1,
                insights_ready=0,
                failures=(),
                items=(
                    AcquisitionItemReport(
                        "abc123DEF45",
                        AcquisitionItemStatus.ACQUIRED,
                        source_sha256="b" * 64,
                    ),
                ),
            )
        ),
    )
    request = parse_source_preview(
        _json_body({"source": stable_plan.source, "language": "fr", "analyze": False})
    )
    fingerprint = str(facade.preview(request)["fingerprint"])

    operation = facade.prepare_acquisition(fingerprint)
    assert executed == []
    result = operation()

    assert executed == [stable_plan]
    assert result["failure_count"] == 0
    assert result["items"] == [
        {
            "video_id": "abc123DEF45",
            "status": "acquired",
            "error_code": None,
            "source_sha256": "b" * 64,
        }
    ]


def test_source_confirmation_performs_no_provider_or_plan_work(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "data")
    plan = _source_plan(paths)
    calls: list[str] = []
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: calls.append("classify") or SourceKind.CHANNEL,
        fetch=lambda source: calls.append("fetch") or VideoListResult(),
        build=lambda **kwargs: calls.append("build") or plan,
        execute=lambda value: calls.append("execute") or AcquisitionReport(0, 0, 0, ()),
    )
    request = parse_source_preview(
        _json_body({"source": plan.source, "language": "fr", "analyze": False})
    )
    fingerprint = str(facade.preview(request)["fingerprint"])
    assert calls == ["classify", "fetch", "build"]

    operation = facade.prepare_acquisition(fingerprint)

    assert calls == ["classify", "fetch", "build"]
    operation()
    assert calls == ["classify", "fetch", "build", "execute"]


def test_source_confirmation_requires_a_cached_preview(tmp_path: Path) -> None:
    facade = SourceAcquisitionFacade(DataPaths.from_root(tmp_path / "data"))

    with pytest.raises(PlanChanged):
        facade.prepare_acquisition("a" * 64)


def test_source_plan_rejects_inconsistent_count_and_fingerprints_every_data_path(
    tmp_path: Path,
) -> None:
    paths = DataPaths.from_root(tmp_path / "data")
    base = _source_plan(paths)
    changed_count = replace(base, selected_count=2)
    changed_paths = replace(
        base,
        data_paths=replace(paths, clips=paths.root / "other-clips"),
    )

    fingerprints = []
    for plan in (base, changed_paths):
        facade = SourceAcquisitionFacade(
            paths,
            classify=lambda source: SourceKind.CHANNEL,
            fetch=lambda source: VideoListResult(),
            build=lambda plan=plan, **kwargs: plan,
            execute=lambda value: AcquisitionReport(0, 0, 0, ()),
        )
        fingerprints.append(
            facade.preview(
                parse_source_preview(
                    _json_body(
                        {"source": base.source, "language": "fr", "analyze": False}
                    )
                )
            )["fingerprint"]
        )

    assert len(set(fingerprints)) == 2
    rejected = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(),
        build=lambda **kwargs: changed_count,
    ).preview(
        parse_source_preview(
            _json_body({"source": base.source, "language": "fr", "analyze": False})
        )
    )
    assert rejected == {"schema_version": 1, "error": {"code": "plan_too_large"}}


def test_source_plan_mutation_after_admission_never_executes(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "data")
    plan = _source_plan(paths)
    executed: list[AcquisitionPlan] = []
    facade = SourceAcquisitionFacade(
        paths,
        classify=lambda source: SourceKind.CHANNEL,
        fetch=lambda source: VideoListResult(),
        build=lambda **kwargs: plan,
        execute=lambda value: executed.append(value) or AcquisitionReport(0, 0, 0, ()),
    )
    request = parse_source_preview(
        _json_body({"source": plan.source, "language": "fr", "analyze": False})
    )
    fingerprint = str(facade.preview(request)["fingerprint"])
    operation = facade.prepare_acquisition(fingerprint)
    plan.selected_videos[0].title = "mutated after admission"

    result = operation()

    assert result == {"schema_version": 1, "error": {"code": "plan_changed"}}
    assert executed == []
