"""Click adapter for durable local research assessment."""

from __future__ import annotations

import json
from uuid import uuid4

import click

from .config import load_config
from .research.assessment import SQLiteEvidenceReader
from .research.models import FreshnessProfile
from .research.store import ResearchStore
from .research.workflow import ResearchResponse, ResearchWorkflow, validate_start_request


_QUESTION = "Is this evidence sufficient, or should I search YouTube for newer sources?"


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error(*, as_json: bool, code: str, message: str) -> None:
    if as_json:
        click.echo(_json({"error": {"code": code}, "schema_version": 1}))
        raise click.exceptions.Exit(1)
    raise click.ClickException(message)


def _workflow() -> ResearchWorkflow:
    """Load only configured local paths and construct the application service."""
    paths = load_config({}).data_paths
    store = ResearchStore(paths.research_database)
    reader = SQLiteEvidenceReader(
        search_database=paths.search_database,
        catalog_database=paths.catalog_database,
    )
    return ResearchWorkflow(
        store=store,
        evidence_reader=reader,
        session_id_factory=lambda: uuid4().hex,
    )


def _emit(response: ResearchResponse, *, as_json: bool) -> None:
    payload = response.to_dict()
    if payload["error_code"] == "local_index_unavailable":
        _error(
            as_json=as_json,
            code="local_index_unavailable",
            message="Local evidence is unavailable. Retry after rebuilding the local index.",
        )
        return
    if as_json:
        click.echo(_json(payload))
        return

    session = payload["session"]
    assert isinstance(session, dict)
    click.echo(f"Session: {session['session_id']}")
    click.echo(f"State: {session['state']}")
    click.echo(f"Revision: {session['revision']}")
    assessment = payload["assessment"]
    if isinstance(assessment, dict):
        coverage = assessment["coverage"]
        freshness = assessment["freshness"]
        assert isinstance(coverage, dict) and isinstance(freshness, dict)
        click.echo(
            "Local evidence: "
            f"{coverage['matched_passages']} passages, "
            f"{coverage['matched_videos']} videos, "
            f"{coverage['distinct_channels']} channels."
        )
        click.echo(
            "Freshness: "
            f"{freshness['reason']} ({freshness['profile']})."
        )
    if payload["error_code"] == "discovery_not_configured":
        click.echo("YouTube discovery is unavailable in this version; no search was run.")
    elif payload["required_user_action"] == "confirm_sufficiency_or_refresh":
        click.echo(_QUESTION)


def _freshness(value: str) -> FreshnessProfile:
    try:
        return FreshnessProfile(value)
    except ValueError:
        raise ValueError("freshness profile is invalid") from None


@click.group("research")
def research_group() -> None:
    """Assess and resume durable local YouTube research."""


@research_group.command("start")
@click.argument("topic")
@click.option("--query", "queries", multiple=True, help="Explicit local retrieval query.")
@click.option("--lang", "languages", multiple=True, help="Exact transcript language filter.")
@click.option("--freshness-profile", default="standard", show_default=True, help="fast, standard, stable, or historical.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def start_command(
    topic: str,
    queries: tuple[str, ...],
    languages: tuple[str, ...],
    freshness_profile: str,
    as_json: bool,
) -> None:
    """Persist a local assessment without accessing YouTube or an LLM."""
    requested_queries = queries or (topic,)
    try:
        profile = _freshness(freshness_profile)
        validate_start_request(
            topic=topic,
            queries=requested_queries,
            languages=languages,
            freshness_profile=profile,
        )
    except (TypeError, ValueError):
        _error(as_json=as_json, code="invalid_request", message="Research request is invalid.")
        return
    try:
        response = _workflow().start(
            topic=topic,
            queries=requested_queries,
            languages=languages,
            freshness_profile=profile,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_state_unavailable",
            message="Local research state is unavailable. Check local database permissions and retry.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("status")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def status_command(session_id: str, as_json: bool) -> None:
    """Show the latest persisted assessment for SESSION_ID."""
    try:
        response = _workflow().status(session_id)
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_session_unavailable",
            message="Research session is unavailable.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("decide")
@click.argument("session_id")
@click.argument("decision")
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--idempotency-key", required=True, help="Unique key for this decision.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def decide_command(
    session_id: str,
    decision: str,
    revision: str,
    idempotency_key: str,
    as_json: bool,
) -> None:
    """Record whether current evidence is sufficient or needs discovery."""
    try:
        expected_revision = int(revision)
        if isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError
        if decision not in {"sufficient", "refresh"} or not idempotency_key:
            raise ValueError
    except ValueError:
        _error(as_json=as_json, code="invalid_decision", message="Research decision is invalid.")
        return
    try:
        response = _workflow().decide(
            session_id,
            expected_revision=expected_revision,
            decision=decision,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_decision_unavailable",
            message="Research decision is invalid or stale.",
        )
        return
    _emit(response, as_json=as_json)
