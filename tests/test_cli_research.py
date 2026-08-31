from __future__ import annotations

import json
from datetime import UTC, datetime

from click.testing import CliRunner

from yt_insights.cli import cli
from yt_insights.research.assessment import AssessmentRetryableError
from yt_insights.research.models import DatabaseSnapshot, PassageEvidence, QuerySpec, VideoEvidence


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
    assert json.loads(refresh.output)["error_code"] == "discovery_not_configured"


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
