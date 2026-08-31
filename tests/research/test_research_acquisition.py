from __future__ import annotations

from datetime import date
from pathlib import Path

from yt_insights.acquisition import (
    AcquisitionItemReport,
    AcquisitionItemStatus,
    AcquisitionReport,
    IndexRefreshReport,
    build_acquisition_plan,
)
from yt_insights.paths import DataPaths
from yt_insights.research.acquisition import (
    CandidateAcquisitionOutcome,
    ResearchAcquisitionService,
)
from yt_insights.research.models import CandidateStatus, ResearchCandidate


def _candidate(video_id: str, title: str) -> ResearchCandidate:
    return ResearchCandidate(
        video_id=video_id,
        title=title,
        channel_id="UCStable",
        channel_title="Stable Channel",
        published_at=date(2026, 8, 20),
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
        matched_queries=("reliable agents",),
        original_rank=1,
        status=CandidateStatus.APPROVED,
    )


def test_acquire_approved_maps_structured_items_and_refreshes_once(tmp_path: Path) -> None:
    paths = DataPaths.from_root(tmp_path / "corpus")
    candidates = (
        _candidate("aaa123DEF45", "Acquired"),
        _candidate("bbb123DEF45", "No transcript"),
        _candidate("ccc123DEF45", "Retryable"),
    )
    planned_urls: list[str] = []
    executed: list[tuple[str, bool, str | None]] = []
    refreshed: list[DataPaths] = []

    def plan_builder(**kwargs: object):
        planned_urls.append(str(kwargs["source"]))
        assert kwargs["analyze"] is False
        return build_acquisition_plan(**kwargs)

    item_by_id = {
        "aaa123DEF45": AcquisitionItemReport(
            "aaa123DEF45",
            AcquisitionItemStatus.ACQUIRED,
            source_sha256="a" * 64,
        ),
        "bbb123DEF45": AcquisitionItemReport(
            "bbb123DEF45",
            AcquisitionItemStatus.NO_TRANSCRIPT,
            error_code="no_transcript",
        ),
        "ccc123DEF45": AcquisitionItemReport(
            "ccc123DEF45",
            AcquisitionItemStatus.FAILED_RETRYABLE,
            error_code="download_failed",
        ),
    }

    def executor(plan, *, cookies_from_browser, refresh_indexes):
        video_id = plan.selected_videos[0].video_id
        executed.append((video_id, refresh_indexes, cookies_from_browser))
        return AcquisitionReport(
            selected=99,
            transcripts_ready=99,
            insights_ready=0,
            failures=("already_present: misleading free-form failure",),
            items=(item_by_id[video_id],),
        )

    def refresh(data_paths: DataPaths):
        refreshed.append(data_paths)
        return IndexRefreshReport(True, True)

    service = ResearchAcquisitionService(
        plan_builder=plan_builder,
        executor=executor,
        index_refresher=refresh,
    )

    outcomes = service.acquire_approved(
        candidates,
        data_paths=paths,
        language="fr",
        cookies_from_browser="safari",
    )

    assert planned_urls == [candidate.watch_url for candidate in candidates]
    assert executed == [
        ("aaa123DEF45", False, "safari"),
        ("bbb123DEF45", False, "safari"),
        ("ccc123DEF45", False, "safari"),
    ]
    assert outcomes == (
        CandidateAcquisitionOutcome(
            "aaa123DEF45", CandidateStatus.ACQUIRED, None, "a" * 64
        ),
        CandidateAcquisitionOutcome(
            "bbb123DEF45", CandidateStatus.NO_TRANSCRIPT, "no_transcript", None
        ),
        CandidateAcquisitionOutcome(
            "ccc123DEF45", CandidateStatus.FAILED_RETRYABLE, "download_failed", None
        ),
    )
    assert refreshed == [paths]


def test_acquire_approved_does_not_refresh_without_a_ready_transcript(
    tmp_path: Path,
) -> None:
    candidate = _candidate("aaa123DEF45", "No transcript")

    def executor(plan, **kwargs):
        return AcquisitionReport(
            selected=1,
            transcripts_ready=0,
            insights_ready=0,
            failures=(),
            items=(
                AcquisitionItemReport(
                    candidate.video_id,
                    AcquisitionItemStatus.NO_TRANSCRIPT,
                    error_code="no_transcript",
                ),
            ),
        )

    service = ResearchAcquisitionService(
        executor=executor,
        index_refresher=lambda paths: (_ for _ in ()).throw(
            AssertionError("refresh must not run")
        ),
    )

    assert service.acquire_approved(
        (candidate,), data_paths=DataPaths.from_root(tmp_path / "corpus"), language="fr"
    ) == (
        CandidateAcquisitionOutcome(
            candidate.video_id,
            CandidateStatus.NO_TRANSCRIPT,
            "no_transcript",
            None,
        ),
    )
