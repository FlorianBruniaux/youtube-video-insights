from __future__ import annotations

import json
from datetime import date

import pytest
from click.testing import CliRunner

from yt_insights.cli import cli
from yt_insights.paths import DataPaths
from yt_insights.research.acquisition import CandidateAcquisitionOutcome
from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.discovery import DiscoveryResult
from yt_insights.research.models import (
    CandidateStatus,
    DatabaseSnapshot,
    PassageEvidence,
    QuerySpec,
    ResearchCandidate,
    VideoEvidence,
)
from yt_insights.research.store import ResearchStore
from yt_insights.research.workflow import ResearchWorkflow

VIDEO_ID = "abc123DEF45"


class FakeEvidenceReader:
    def __init__(self, **_: object) -> None:
        pass

    def capture_snapshot(self) -> DatabaseSnapshot:
        return DatabaseSnapshot("search-generation", "catalog-generation")

    def validate_snapshot(self, snapshot: DatabaseSnapshot) -> None:
        return None

    def search_passages(
        self, query: QuerySpec, *, languages: tuple[str, ...], limit: int
    ) -> tuple[PassageEvidence, ...]:
        return (
            PassageEvidence(
                query=query.text,
                passage_id="passage-1",
                video_id=VIDEO_ID,
                channel_id="UC12345678901234567890AB",
                rank=1,
                url=f"https://youtube.com/watch?v={VIDEO_ID}&t=12s",
                excerpt="Bounded local passage.",
                source_sha256="a" * 64,
            ),
        )

    def search_videos(self, query: QuerySpec, *, limit: int) -> tuple[VideoEvidence, ...]:
        return (
            VideoEvidence(
                query=query.text,
                video_id=VIDEO_ID,
                source_keys=("local",),
                title="Local evidence",
                published_at=None,
                rank=1,
                watch_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
            ),
        )


class FakeDiscoveryProvider:
    def __init__(self, candidates: tuple[ResearchCandidate, ...]) -> None:
        self.candidates = candidates
        self.calls: list[tuple[tuple[QuerySpec, ...], int]] = []

    def discover(
        self, queries: tuple[QuerySpec, ...], *, limit: int = 10
    ) -> DiscoveryResult:
        self.calls.append((queries, limit))
        return DiscoveryResult("yt-dlp", 1, self.candidates, (), True)


class FakeAcquisitionService:
    def __init__(self, outcomes: tuple[CandidateAcquisitionOutcome, ...]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[tuple[str, ...], str, str | None]] = []

    def acquire_approved(
        self,
        candidates: tuple[ResearchCandidate, ...],
        *,
        data_paths: DataPaths,
        language: str,
        cookies_from_browser: str | None = None,
    ) -> tuple[CandidateAcquisitionOutcome, ...]:
        self.calls.append(
            (tuple(candidate.video_id for candidate in candidates), language, cookies_from_browser)
        )
        return self.outcomes


def _candidate(video_id: str) -> ResearchCandidate:
    return ResearchCandidate(
        video_id=video_id,
        title=f"Candidate {video_id}",
        channel_id="UC12345678901234567890AB",
        channel_title="Candidate channel",
        published_at=date(2026, 8, 30),
        watch_url=f"https://www.youtube.com/watch?v={video_id}",
        matched_queries=("Local evidence",),
        original_rank=1,
        status=CandidateStatus.CANDIDATE,
    )


def _configure_discovery_workflow(
    tmp_path,
    monkeypatch,
    candidates: tuple[ResearchCandidate, ...],
    *,
    acquisition_service: object | None = None,
    index_refresher: object | None = None,
) -> FakeDiscoveryProvider:
    from yt_insights import cli_research

    provider = FakeDiscoveryProvider(candidates)
    kwargs: dict[str, object] = {}
    if acquisition_service is not None:
        kwargs["acquisition_service"] = acquisition_service
    if index_refresher is not None:
        kwargs["index_refresher"] = index_refresher
    workflow = ResearchWorkflow(
        store=ResearchStore(tmp_path / "research.sqlite3"),
        evidence_reader=FakeEvidenceReader(),
        discovery_provider=provider,
        data_paths=DataPaths.from_root(tmp_path / "data"),
        session_id_factory=lambda: "01K4RESEARCH0000000000000000",
        **kwargs,
    )
    monkeypatch.setattr(cli_research, "_workflow", lambda: workflow)
    return provider


def _discover_candidates(runner: CliRunner) -> str:
    session_id = "01K4RESEARCH0000000000000000"
    start = runner.invoke(cli, ["research", "start", "Local evidence", "--json"])
    assert start.exit_code == 0, start.output
    refresh = runner.invoke(
        cli,
        [
            "research",
            "decide",
            session_id,
            "refresh",
            "--revision",
            "1",
            "--idempotency-key",
            "refresh-key",
            "--json",
        ],
    )
    assert refresh.exit_code == 0, refresh.output
    discovered = runner.invoke(
        cli,
        ["research", "discover", session_id, "--revision", "2", "--json"],
    )
    assert discovered.exit_code == 0, discovered.output
    return session_id


def test_research_json_contract_is_stable_and_never_prompts(tmp_path, monkeypatch) -> None:
    from yt_insights import cli_research
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(cli_research, "SQLiteEvidenceReader", FakeEvidenceReader)
    runner = CliRunner()
    environment = {"YT_INSIGHTS_DATA_ROOT": str(tmp_path / "data")}

    start = runner.invoke(
        cli,
        ["research", "start", "Local evidence", "--lang", "fr", "--json"],
        env=environment,
    )

    assert start.exit_code == 0, start.output
    payload = json.loads(start.output)
    assert start.output == json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    assert payload["session"]["queries"] == ["Local evidence"]
    assert payload["session"]["languages"] == ["fr"]
    assert "Is this evidence sufficient" not in start.output

    status = runner.invoke(cli, ["research", "status", payload["session"]["session_id"], "--json"], env=environment)
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["assessment"] == payload["assessment"]

    refresh = runner.invoke(
        cli,
        ["research", "decide", payload["session"]["session_id"], "refresh", "--revision", "1", "--idempotency-key", "refresh-key", "--json"],
        env=environment,
    )
    assert refresh.exit_code == 0, refresh.output
    assert json.loads(refresh.output)["error_code"] is None


def test_research_human_assessment_ends_with_the_mandatory_question(tmp_path, monkeypatch) -> None:
    from yt_insights import cli_research
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(cli_research, "SQLiteEvidenceReader", FakeEvidenceReader)

    result = CliRunner().invoke(
        cli,
        ["research", "start", "Local evidence"],
        env={"YT_INSIGHTS_DATA_ROOT": str(tmp_path / "data")},
    )

    assert result.exit_code == 0, result.output
    assert result.output.endswith(
        "Is this evidence sufficient, or should I search YouTube for newer sources?\n"
    )


def test_research_invalid_input_and_database_failures_do_not_echo_untrusted_query(tmp_path, monkeypatch) -> None:
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    secret = "QUERY-CANARY-DO-NOT-ECHO"
    result = CliRunner().invoke(
        cli,
        ["research", "start", "Local evidence", "--query", secret, "--query", secret],
        env={"YT_INSIGHTS_DATA_ROOT": str(tmp_path / "data")},
    )

    assert result.exit_code != 0
    assert secret not in result.output


def test_research_json_index_failure_uses_a_bounded_error_envelope(tmp_path, monkeypatch) -> None:
    from yt_insights import cli_research
    from yt_insights import config as config_module

    class FailingReader(FakeEvidenceReader):
        def capture_snapshot(self) -> DatabaseSnapshot:
            raise AssessmentRetryableError("private database details")

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(cli_research, "SQLiteEvidenceReader", FailingReader)
    secret = "QUERY-CANARY-LOCAL-INDEX"
    result = CliRunner().invoke(
        cli,
        ["research", "start", "Local evidence", "--query", secret, "--json"],
        env={"YT_INSIGHTS_DATA_ROOT": str(tmp_path / "data")},
    )

    assert result.exit_code != 0
    assert json.loads(result.output) == {
        "error": {"code": "local_index_unavailable"},
        "schema_version": 1,
    }
    assert secret not in result.output


def test_research_candidate_commands_keep_discovery_separate_and_render_metadata(
    tmp_path, monkeypatch
) -> None:
    candidate = _candidate("zyx987WVUT0")
    provider = _configure_discovery_workflow(tmp_path, monkeypatch, (candidate,))
    runner = CliRunner()

    session_id = _discover_candidates(runner)
    candidates = runner.invoke(cli, ["research", "candidates", session_id])

    assert provider.calls == [((QuerySpec("Local evidence"),), 10)]
    assert candidates.exit_code == 0, candidates.output
    assert "Date: 2026-08-30" in candidates.output
    assert "Channel: Candidate channel" in candidates.output
    assert "Title: Candidate zyx987WVUT0" in candidates.output
    assert "URL: https://www.youtube.com/watch?v=zyx987WVUT0" in candidates.output
    assert "Matching query: Local evidence" in candidates.output
    assert "description" not in candidates.output.casefold()


def test_research_discover_cli_bounds_unexpected_provider_exception(
    tmp_path, monkeypatch
) -> None:
    provider = _configure_discovery_workflow(
        tmp_path,
        monkeypatch,
        (_candidate("zyx987WVUT0"),),
    )

    def fail_discovery(*_args: object, **_kwargs: object) -> DiscoveryResult:
        raise LookupError("private provider lookup failure")

    monkeypatch.setattr(provider, "discover", fail_discovery)
    runner = CliRunner()
    started = runner.invoke(cli, ["research", "start", "Local evidence", "--json"])
    session_id = json.loads(started.output)["session"]["session_id"]
    runner.invoke(
        cli,
        [
            "research",
            "decide",
            session_id,
            "refresh",
            "--revision",
            "1",
            "--idempotency-key",
            "refresh-key",
            "--json",
        ],
    )

    result = runner.invoke(
        cli,
        ["research", "discover", session_id, "--revision", "2", "--json"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "error": {"code": "discovery_unavailable"},
        "schema_version": 1,
    }
    status = runner.invoke(cli, ["research", "status", session_id, "--json"])
    assert json.loads(status.output)["session"]["state"] == "failed_retryable"
    assert "private provider lookup failure" not in result.output


@pytest.mark.parametrize(
    "video_ids",
    [
        ("zyx987WVUT0",),
        (
            "zyx987WVUT0",
            "zyx987WVUT1",
            "zyx987WVUT2",
            "zyx987WVUT3",
            "zyx987WVUT4",
        ),
    ],
)
def test_research_approve_accepts_one_to_five_current_candidate_ids(
    tmp_path, monkeypatch, video_ids: tuple[str, ...]
) -> None:
    _configure_discovery_workflow(
        tmp_path, monkeypatch, tuple(_candidate(video_id) for video_id in video_ids)
    )
    runner = CliRunner()
    session_id = _discover_candidates(runner)

    approved = runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            *video_ids,
            "--revision",
            "3",
            "--idempotency-key",
            "approve-key",
            "--json",
        ],
    )

    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["session"]["state"] == "acquiring"


def test_research_approve_rejects_unknown_and_stale_candidate_selection(
    tmp_path, monkeypatch
) -> None:
    _configure_discovery_workflow(tmp_path, monkeypatch, (_candidate("zyx987WVUT0"),))
    runner = CliRunner()
    session_id = _discover_candidates(runner)

    unknown = runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            "unknown0000",
            "--revision",
            "3",
            "--idempotency-key",
            "unknown-key",
            "--json",
        ],
    )
    stale = runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            "zyx987WVUT0",
            "--revision",
            "2",
            "--idempotency-key",
            "stale-key",
            "--json",
        ],
    )

    assert unknown.exit_code == 1
    assert json.loads(unknown.output) == {
        "error": {"code": "research_candidate_decision_unavailable"},
        "schema_version": 1,
    }
    assert stale.exit_code == 1
    assert json.loads(stale.output) == {
        "error": {"code": "research_candidate_decision_unavailable"},
        "schema_version": 1,
    }


def test_research_cancel_replays_only_the_original_candidate_decision(
    tmp_path, monkeypatch
) -> None:
    _configure_discovery_workflow(tmp_path, monkeypatch, (_candidate("zyx987WVUT0"),))
    runner = CliRunner()
    session_id = _discover_candidates(runner)

    cancelled = runner.invoke(
        cli,
        [
            "research",
            "cancel",
            session_id,
            "--revision",
            "3",
            "--idempotency-key",
            "cancel-key",
            "--json",
        ],
    )
    replayed = runner.invoke(
        cli,
        [
            "research",
            "cancel",
            session_id,
            "--revision",
            "3",
            "--idempotency-key",
            "cancel-key",
            "--json",
        ],
    )
    changed_payload = runner.invoke(
        cli,
        [
            "research",
            "cancel",
            session_id,
            "--revision",
            "4",
            "--idempotency-key",
            "cancel-key",
            "--json",
        ],
    )
    new_key = runner.invoke(
        cli,
        [
            "research",
            "cancel",
            session_id,
            "--revision",
            "4",
            "--idempotency-key",
            "different-cancel-key",
            "--json",
        ],
    )

    assert cancelled.exit_code == 0, cancelled.output
    assert json.loads(cancelled.output)["session"]["state"] == "cancelled"
    assert replayed.exit_code == 0, replayed.output
    assert json.loads(replayed.output) == json.loads(cancelled.output)
    for rejected in (changed_payload, new_key):
        assert rejected.exit_code == 1
        assert json.loads(rejected.output) == {
            "error": {"code": "research_candidate_decision_unavailable"},
            "schema_version": 1,
        }


def test_research_cancel_rejects_sufficiency_confirmation_state(
    tmp_path, monkeypatch
) -> None:
    _configure_discovery_workflow(tmp_path, monkeypatch, (_candidate("zyx987WVUT0"),))
    runner = CliRunner()
    started = runner.invoke(cli, ["research", "start", "Local evidence", "--json"])
    assert started.exit_code == 0, started.output
    session_id = json.loads(started.output)["session"]["session_id"]

    cancelled = runner.invoke(
        cli,
        [
            "research",
            "cancel",
            session_id,
            "--revision",
            "1",
            "--idempotency-key",
            "cancel-sufficiency",
            "--json",
        ],
    )

    assert cancelled.exit_code == 1
    assert json.loads(cancelled.output) == {
        "error": {"code": "research_candidate_decision_unavailable"},
        "schema_version": 1,
    }


def test_research_sufficient_stale_decision_and_unknown_status_are_bounded(
    tmp_path, monkeypatch
) -> None:
    from yt_insights import cli_research
    from yt_insights import config as config_module

    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    monkeypatch.setattr(cli_research, "SQLiteEvidenceReader", FakeEvidenceReader)
    runner = CliRunner()
    environment = {"YT_INSIGHTS_DATA_ROOT": str(tmp_path / "data")}
    started = runner.invoke(
        cli, ["research", "start", "Local evidence", "--json"], env=environment
    )
    assert started.exit_code == 0, started.output
    session_id = json.loads(started.output)["session"]["session_id"]

    sufficient = runner.invoke(
        cli,
        [
            "research",
            "decide",
            session_id,
            "sufficient",
            "--revision",
            "1",
            "--idempotency-key",
            "sufficient-key",
            "--json",
        ],
        env=environment,
    )
    stale = runner.invoke(
        cli,
        [
            "research",
            "decide",
            session_id,
            "sufficient",
            "--revision",
            "1",
            "--idempotency-key",
            "stale-key",
            "--json",
        ],
        env=environment,
    )
    unknown = runner.invoke(
        cli, ["research", "status", "unknown-session", "--json"], env=environment
    )

    assert json.loads(sufficient.output)["session"]["state"] == "completed"
    assert stale.exit_code == 1
    assert json.loads(stale.output) == {
        "error": {"code": "research_decision_unavailable"},
        "schema_version": 1,
    }
    assert unknown.exit_code == 1
    assert json.loads(unknown.output) == {
        "error": {"code": "research_session_unavailable"},
        "schema_version": 1,
    }


def test_research_acquire_cli_uses_exact_approved_batch_and_returns_question(
    tmp_path, monkeypatch
) -> None:
    video_id = "zyx987WVUT0"
    candidate = _candidate(video_id)
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    refresh_calls: list[DataPaths] = []
    _configure_discovery_workflow(
        tmp_path,
        monkeypatch,
        (candidate,),
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    runner = CliRunner()
    session_id = _discover_candidates(runner)
    approved = runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            video_id,
            "--revision",
            "3",
            "--idempotency-key",
            "approve-key",
            "--json",
        ],
    )
    assert approved.exit_code == 0, approved.output

    acquired = runner.invoke(
        cli,
        [
            "research",
            "acquire",
            session_id,
            "--revision",
            "4",
            "--idempotency-key",
            "acquire-key",
            "--lang",
            "en",
            "--cookies-from-browser",
            "firefox",
            "--json",
        ],
    )

    assert acquired.exit_code == 0, acquired.output
    payload = json.loads(acquired.output)
    assert payload["session"]["state"] == "awaiting_sufficiency_confirmation"
    assert payload["required_user_action"] == "confirm_sufficiency_or_refresh"
    assert acquisition.calls == [((video_id,), "en", "firefox")]
    assert len(refresh_calls) == 1


def test_research_acquire_cli_rejects_stale_revision_without_network(
    tmp_path, monkeypatch
) -> None:
    video_id = "zyx987WVUT0"
    candidate = _candidate(video_id)
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    _configure_discovery_workflow(
        tmp_path,
        monkeypatch,
        (candidate,),
        acquisition_service=acquisition,
        index_refresher=lambda paths: None,
    )
    runner = CliRunner()
    session_id = _discover_candidates(runner)
    runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            video_id,
            "--revision",
            "3",
            "--idempotency-key",
            "approve-key",
            "--json",
        ],
    )

    stale = runner.invoke(
        cli,
        [
            "research",
            "acquire",
            session_id,
            "--revision",
            "3",
            "--idempotency-key",
            "stale-acquire-key",
            "--json",
        ],
    )

    assert stale.exit_code == 1
    assert json.loads(stale.output) == {
        "error": {"code": "research_acquisition_unavailable"},
        "schema_version": 1,
    }
    assert acquisition.calls == []


def test_research_retry_cli_reindexes_without_reacquiring(tmp_path, monkeypatch) -> None:
    video_id = "zyx987WVUT0"
    candidate = _candidate(video_id)
    acquisition = FakeAcquisitionService(
        (
            CandidateAcquisitionOutcome(
                video_id,
                CandidateStatus.ACQUIRED,
                None,
                "b" * 64,
            ),
        )
    )
    refresh_count = 0

    def flaky_refresh(paths: DataPaths) -> None:
        nonlocal refresh_count
        refresh_count += 1
        if refresh_count == 1:
            raise RuntimeError("private index error")

    _configure_discovery_workflow(
        tmp_path,
        monkeypatch,
        (candidate,),
        acquisition_service=acquisition,
        index_refresher=flaky_refresh,
    )
    runner = CliRunner()
    session_id = _discover_candidates(runner)
    runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            video_id,
            "--revision",
            "3",
            "--idempotency-key",
            "approve-key",
            "--json",
        ],
    )
    failed = runner.invoke(
        cli,
        [
            "research",
            "acquire",
            session_id,
            "--revision",
            "4",
            "--idempotency-key",
            "acquire-key",
            "--json",
        ],
    )
    assert failed.exit_code == 1

    retried = runner.invoke(
        cli,
        [
            "research",
            "retry",
            session_id,
            "--revision",
            "6",
            "--idempotency-key",
            "retry-key",
            "--json",
        ],
    )

    assert retried.exit_code == 0, retried.output
    assert json.loads(retried.output)["session"]["state"] == "awaiting_sufficiency_confirmation"
    assert len(acquisition.calls) == 1
    assert refresh_count == 2


def test_research_retry_cli_reacquires_failed_attempt_in_same_command_and_replays(
    tmp_path, monkeypatch
) -> None:
    video_id = "zyx987WVUT0"
    candidate = _candidate(video_id)

    class FailingOnceAcquisition:
        def __init__(self) -> None:
            self.calls: list[tuple[tuple[str, ...], str, str | None]] = []

        def acquire_approved(
            self,
            candidates: tuple[ResearchCandidate, ...],
            *,
            data_paths: DataPaths,
            language: str,
            cookies_from_browser: str | None = None,
        ) -> tuple[CandidateAcquisitionOutcome, ...]:
            video_ids = tuple(candidate.video_id for candidate in candidates)
            self.calls.append((video_ids, language, cookies_from_browser))
            if len(self.calls) == 1:
                raise LookupError("private first attempt failure")
            return (
                CandidateAcquisitionOutcome(
                    video_id,
                    CandidateStatus.ACQUIRED,
                    None,
                    "b" * 64,
                ),
            )

    acquisition = FailingOnceAcquisition()
    refresh_calls: list[DataPaths] = []
    _configure_discovery_workflow(
        tmp_path,
        monkeypatch,
        (candidate,),
        acquisition_service=acquisition,
        index_refresher=lambda paths: refresh_calls.append(paths),
    )
    runner = CliRunner()
    session_id = _discover_candidates(runner)
    runner.invoke(
        cli,
        [
            "research",
            "approve",
            session_id,
            video_id,
            "--revision",
            "3",
            "--idempotency-key",
            "approve-key",
            "--json",
        ],
    )
    failed = runner.invoke(
        cli,
        [
            "research",
            "acquire",
            session_id,
            "--revision",
            "4",
            "--idempotency-key",
            "acquire-key",
            "--lang",
            "en",
            "--cookies-from-browser",
            "firefox:research",
            "--json",
        ],
    )
    assert failed.exit_code == 1
    status = runner.invoke(cli, ["research", "status", session_id, "--json"])
    failed_revision = json.loads(status.output)["session"]["revision"]

    retry_args = [
        "research",
        "retry",
        session_id,
        "--revision",
        str(failed_revision),
        "--idempotency-key",
        "retry-key",
        "--json",
    ]
    retried = runner.invoke(cli, retry_args)
    replayed = runner.invoke(cli, retry_args)
    changed_payload = runner.invoke(
        cli,
        [
            "research",
            "retry",
            session_id,
            "--revision",
            str(failed_revision + 1),
            "--idempotency-key",
            "retry-key",
            "--json",
        ],
    )

    assert retried.exit_code == 0, retried.output
    assert replayed.exit_code == 0, replayed.output
    assert json.loads(replayed.output) == json.loads(retried.output)
    assert json.loads(retried.output)["session"]["state"] == (
        "awaiting_sufficiency_confirmation"
    )
    assert acquisition.calls == [
        ((video_id,), "en", "firefox:research"),
        ((video_id,), "en", "firefox:research"),
    ]
    assert len(refresh_calls) == 1
    assert changed_payload.exit_code == 1
    assert json.loads(changed_payload.output) == {
        "error": {"code": "research_retry_unavailable"},
        "schema_version": 1,
    }


def test_research_acquisition_gate_warnings_are_bounded_and_do_not_block() -> None:
    from yt_insights.cli_research import _gate_warnings

    assert _gate_warnings() == (
        "gate_relevance_pilot_unknown",
        "gate_global_activation_not_ready",
        "gate_code_sha_unverified",
    )
