"""Acquire explicitly approved research candidates without model inference."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from yt_insights.acquisition import (
    AcquisitionItemStatus,
    AcquisitionPlan,
    AcquisitionReport,
    build_acquisition_plan,
    execute_acquisition,
)
from yt_insights.downloader import VideoInfo
from yt_insights.paths import DataPaths

from .models import CandidateStatus, ResearchCandidate


@dataclass(frozen=True, slots=True)
class CandidateAcquisitionOutcome:
    video_id: str
    status: CandidateStatus
    error_code: str | None
    source_sha256: str | None


_CANDIDATE_STATUS_BY_ITEM_STATUS = {
    AcquisitionItemStatus.ACQUIRED: CandidateStatus.ACQUIRED,
    AcquisitionItemStatus.ALREADY_PRESENT: CandidateStatus.ALREADY_PRESENT,
    AcquisitionItemStatus.NO_TRANSCRIPT: CandidateStatus.NO_TRANSCRIPT,
    AcquisitionItemStatus.FAILED_RETRYABLE: CandidateStatus.FAILED_RETRYABLE,
}


class ResearchAcquisitionService:
    """Run one acquisition plan per approved candidate without publishing indexes."""

    def __init__(
        self,
        *,
        plan_builder: Callable[..., AcquisitionPlan] = build_acquisition_plan,
        executor: Callable[..., AcquisitionReport] = execute_acquisition,
    ) -> None:
        self._plan_builder = plan_builder
        self._executor = executor

    def acquire_approved(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        data_paths: DataPaths,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> tuple[CandidateAcquisitionOutcome, ...]:
        if not isinstance(candidates, tuple):
            raise TypeError("candidates must be a tuple")
        for candidate in candidates:
            if not isinstance(candidate, ResearchCandidate):
                raise TypeError("candidates must contain ResearchCandidate values")
            if candidate.status is not CandidateStatus.APPROVED:
                raise ValueError("all acquisition candidates must be approved")

        plans = tuple(
            self._plan_builder(
                source=candidate.watch_url,
                data_paths=data_paths,
                language=language,
                analyze=False,
                discovered=(
                    VideoInfo(
                        video_id=candidate.video_id,
                        title=candidate.title,
                        upload_date=(
                            candidate.published_at.strftime("%Y%m%d")
                            if candidate.published_at is not None
                            else ""
                        ),
                        channel_id=candidate.channel_id or "",
                        channel_title=candidate.channel_title or "",
                    ),
                ),
            )
            for candidate in candidates
        )

        outcomes: list[CandidateAcquisitionOutcome] = []
        for candidate, plan in zip(candidates, plans, strict=True):
            report = self._executor(
                plan,
                cookies_from_browser=cookies_from_browser,
                refresh_indexes=False,
            )
            if len(report.items) != 1 or report.items[0].video_id != candidate.video_id:
                raise ValueError("single-video acquisition returned invalid item identity")
            item = report.items[0]
            outcomes.append(
                CandidateAcquisitionOutcome(
                    video_id=item.video_id,
                    status=_CANDIDATE_STATUS_BY_ITEM_STATUS[item.status],
                    error_code=item.error_code,
                    source_sha256=item.source_sha256,
                )
            )

        return tuple(outcomes)
