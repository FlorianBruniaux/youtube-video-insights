"""Click adapter for durable local research assessment."""

from __future__ import annotations

import json
import re
import unicodedata
from contextlib import ExitStack
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import click

from . import __version__
from .catalog import Catalog
from .config import load_config
from .research.acquisition import ResearchAcquisitionService
from .research.assessment import SQLiteEvidenceReader
from .research.discovery import YtDlpDiscoveryProvider
from .research.dossier import (
    DossierExportRequest,
    DossierExportResult,
    bind_dossier_output_root,
)
from .research.models import FreshnessProfile
from .research.store import ResearchStore
from .research.workflow import (
    ResearchResponse,
    ResearchWorkflow,
    validate_start_request,
)

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

    def existing_video_ids(video_ids: tuple[str, ...]) -> frozenset[str]:
        with Catalog.open_read_only(paths.catalog_database) as catalog:
            return catalog.existing_video_ids(video_ids)

    return ResearchWorkflow(
        store=store,
        evidence_reader=reader,
        discovery_provider=YtDlpDiscoveryProvider(existing_ids=existing_video_ids),
        acquisition_service=ResearchAcquisitionService(),
        data_paths=paths,
        session_id_factory=lambda: uuid4().hex,
    )


def _emit(
    response: ResearchResponse,
    *,
    as_json: bool,
    warnings: tuple[str, ...] = (),
) -> None:
    payload = response.to_dict()
    if payload["error_code"] == "local_index_unavailable":
        _error(
            as_json=as_json,
            code="local_index_unavailable",
            message="Local evidence is unavailable. Retry after rebuilding the local index.",
        )
        return
    if payload["error_code"] == "discovery_unavailable":
        _error(
            as_json=as_json,
            code="discovery_unavailable",
            message="Candidate discovery is unavailable. Retry from the current session state.",
        )
        return
    if payload["error_code"] == "acquisition_unavailable":
        _error(
            as_json=as_json,
            code="acquisition_unavailable",
            message="Approved source acquisition failed. Retry from the persisted session state.",
        )
        return
    if payload["error_code"] == "acquisition_in_progress":
        _error(
            as_json=as_json,
            code="acquisition_in_progress",
            message="This acquisition is already running. Retry after it finishes or exits.",
        )
        return
    if payload["error_code"] == "retry_in_progress":
        _error(
            as_json=as_json,
            code="retry_in_progress",
            message="This retry is already running. Retry after it finishes or exits.",
        )
        return
    if payload["error_code"] == "index_refresh_failed":
        _error(
            as_json=as_json,
            code="index_refresh_failed",
            message="The acquired sources were kept, but index publication failed. Retry reindexing.",
        )
        return
    if as_json:
        if warnings:
            payload["warnings"] = list(warnings)
        click.echo(_json(payload))
        return

    for warning in warnings:
        click.echo(f"Warning: {warning}", err=True)

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
    candidates = payload["candidates"]
    if isinstance(candidates, list):
        for candidate in candidates:
            assert isinstance(candidate, dict)
            channel = candidate["channel_title"] or candidate["channel_id"] or "Unknown"
            published_at = candidate["published_at"] or "Unknown"
            click.echo(f"Date: {published_at}")
            click.echo(f"Channel: {channel}")
            click.echo(f"Title: {candidate['title']}")
            click.echo(f"URL: {candidate['watch_url']}")
            click.echo(
                "Matching query: " + ", ".join(candidate["matched_queries"])
            )
    if payload["required_user_action"] == "confirm_sufficiency_or_refresh":
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


def _revision(value: str) -> int:
    expected_revision = int(value)
    if isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValueError
    return expected_revision


@research_group.command("discover")
@click.argument("session_id")
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def discover_command(session_id: str, revision: str, as_json: bool) -> None:
    """Discover metadata candidates after a persisted refresh authorization."""
    try:
        expected_revision = _revision(revision)
    except (TypeError, ValueError):
        _error(as_json=as_json, code="invalid_discovery_request", message="Discovery request is invalid.")
        return
    try:
        response = _workflow().discover(session_id, expected_revision=expected_revision)
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_discovery_unavailable",
            message="Candidate discovery is unavailable or stale.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("candidates")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def candidates_command(session_id: str, as_json: bool) -> None:
    """Show the latest persisted discovery candidates for SESSION_ID."""
    try:
        response = _workflow().candidates(session_id)
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_session_unavailable",
            message="Research session is unavailable.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("approve")
@click.argument("session_id")
@click.argument("video_ids", nargs=-1, required=True)
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--idempotency-key", required=True, help="Unique key for this approval.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def approve_command(
    session_id: str,
    video_ids: tuple[str, ...],
    revision: str,
    idempotency_key: str,
    as_json: bool,
) -> None:
    """Approve one to five candidates for a later acquisition command."""
    try:
        expected_revision = _revision(revision)
        if not 1 <= len(video_ids) <= 5 or not idempotency_key:
            raise ValueError
    except (TypeError, ValueError):
        _error(
            as_json=as_json,
            code="invalid_candidate_decision",
            message="Candidate approval is invalid.",
        )
        return
    try:
        response = _workflow().approve(
            session_id,
            expected_revision=expected_revision,
            video_ids=video_ids,
            idempotency_key=idempotency_key,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_candidate_decision_unavailable",
            message="Candidate approval is invalid or stale.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("cancel")
@click.argument("session_id")
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--idempotency-key", required=True, help="Unique key for this cancellation.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def cancel_command(
    session_id: str, revision: str, idempotency_key: str, as_json: bool
) -> None:
    """Cancel only a session that is waiting for a human decision."""
    try:
        expected_revision = _revision(revision)
        if not idempotency_key:
            raise ValueError
    except (TypeError, ValueError):
        _error(
            as_json=as_json,
            code="invalid_candidate_decision",
            message="Cancellation request is invalid.",
        )
        return
    try:
        response = _workflow().cancel(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_candidate_decision_unavailable",
            message="Cancellation is invalid or stale.",
        )
        return
    _emit(response, as_json=as_json)


@research_group.command("acquire")
@click.argument("session_id")
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--idempotency-key", required=True, help="Unique key for this acquisition.")
@click.option("--lang", "language", default="fr", show_default=True)
@click.option("--cookies-from-browser", default=None, metavar="BROWSER")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def acquire_command(
    session_id: str,
    revision: str,
    idempotency_key: str,
    language: str,
    cookies_from_browser: str | None,
    as_json: bool,
) -> None:
    """Acquire only the exact candidates approved at the current revision."""
    try:
        expected_revision = _revision(revision)
        if not idempotency_key or not language.strip():
            raise ValueError
    except (TypeError, ValueError):
        _error(
            as_json=as_json,
            code="invalid_acquisition_request",
            message="Research acquisition request is invalid.",
        )
        return
    try:
        response = _workflow().acquire(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            language=language,
            cookies_from_browser=cookies_from_browser,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_acquisition_unavailable",
            message="Research acquisition is unavailable, invalid, or stale.",
        )
        return
    _emit(response, as_json=as_json, warnings=_gate_warnings())


@research_group.command("retry")
@click.argument("session_id")
@click.option("--revision", required=True, help="Current session revision.")
@click.option("--idempotency-key", required=True, help="Unique key for this retry.")
@click.option("--json", "as_json", is_flag=True, help="Emit stable machine-readable JSON.")
def retry_command(
    session_id: str,
    revision: str,
    idempotency_key: str,
    as_json: bool,
) -> None:
    """Resume a failed stage or a lock-free orphaned acquisition."""
    try:
        expected_revision = _revision(revision)
        if not idempotency_key:
            raise ValueError
    except (TypeError, ValueError):
        _error(
            as_json=as_json,
            code="invalid_retry_request",
            message="Research retry request is invalid.",
        )
        return
    try:
        response = _workflow().retry(
            session_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        _error(
            as_json=as_json,
            code="research_retry_unavailable",
            message="Research retry is unavailable, invalid, or stale.",
        )
        return
    _emit(response, as_json=as_json, warnings=_gate_warnings())


def _explicit_export_path(value: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("export output is invalid")
    destination = Path(value)
    if not destination.is_absolute() or ".." in destination.parts:
        raise ValueError("export output must be an absolute confined path")
    return destination


def _topic_slug(topic: str) -> str:
    ascii_topic = unicodedata.normalize("NFKD", topic).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_topic.casefold()).strip("-")
    return slug[:64].rstrip("-") or "research"


def _emit_export(result: DossierExportResult, *, as_json: bool) -> None:
    if as_json:
        click.echo(_json({"schema_version": 1, **result.to_dict()}))
        return
    click.echo(f"Directory: {result.directory}")
    click.echo(f"Manifest SHA-256: {result.manifest_sha256}")
    click.echo(f"Dossier SHA-256: {result.dossier_sha256}")


@research_group.command("export")
@click.argument("session_id")
@click.option("--output", default=None, metavar="DIRECTORY")
@click.option("--force", is_flag=True, help="Replace a validated prior dossier.")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit stable machine-readable JSON.",
)
def export_command(
    session_id: str,
    output: str | None,
    force: bool,
    as_json: bool,
) -> None:
    """Export stored evidence without generating model-authored prose."""
    root_stack = ExitStack()
    bound_root = None
    if output is not None:
        try:
            output_directory = _explicit_export_path(output)
        except (TypeError, ValueError):
            _error(
                as_json=as_json,
                code="invalid_export_request",
                message="Research export request is invalid.",
            )
            return
    else:
        try:
            configured_root = load_config({}).research_output_root
            if configured_root is None or not configured_root.is_absolute():
                raise ValueError("research output root is not configured")
            bound_root = root_stack.enter_context(
                bind_dossier_output_root(configured_root)
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            root_stack.close()
            _error(
                as_json=as_json,
                code="invalid_export_request",
                message="Research export requires an absolute configured output root or --output.",
            )
            return
        output_directory = configured_root

    try:
        workflow = _workflow()
        if output is None:
            if bound_root is None:
                raise RuntimeError("research output root is unavailable")
            response = workflow.status(session_id)
            topic_directory = bound_root.ensure_topic_directory(
                _topic_slug(response.session.topic)
            )
            created_date = (
                response.session.created_at.astimezone(UTC).date().isoformat()
            )
            output_directory = topic_directory / (
                f"{created_date}-{response.session.session_id}"
            )
        result = workflow.export(
            DossierExportRequest(
                session_id=session_id,
                output_directory=output_directory,
                force=force,
            ),
            package_version=__version__,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        _error(
            as_json=as_json,
            code="research_export_unavailable",
            message="Research export is unavailable for this session or destination.",
        )
        return
    finally:
        root_stack.close()
    _emit_export(result, as_json=as_json)


def _gate_warnings() -> tuple[str, ...]:
    """Return bounded non-PASS Task 0 statuses when checked-in evidence exists."""
    evidence_path = (
        Path(__file__).resolve().parents[2]
        / "plans"
        / "evidence"
        / "2026-08-31-cumulative-research-gates.json"
    )
    try:
        if not evidence_path.is_file() or evidence_path.stat().st_size > 1_000_000:
            return ()
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        gates = payload["gates"]
        if not isinstance(gates, dict):
            return ()
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ()

    warnings: list[str] = []
    for name in ("relevance_pilot", "discovery_probe", "refresh_performance"):
        status = gates.get(name)
        if status != "PASS" and isinstance(status, str) and status in {"UNKNOWN", "FAIL"}:
            warnings.append(f"gate_{name}_{status.casefold()}")
    if gates.get("global_activation_ready") is not True:
        warnings.append("gate_global_activation_not_ready")
    if isinstance(payload.get("code_sha"), str):
        warnings.append("gate_code_sha_unverified")
    return tuple(warnings)
