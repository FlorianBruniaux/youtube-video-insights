"""Deterministic, local-only exports of bounded research evidence."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .models import (
    CandidateStatus,
    PassageEvidence,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchSession,
    ResearchState,
    SessionHistory,
)
from .store import ResearchStore


_FORMAT_VERSION = 1
_DOSSIER_NAME = "dossier.md"
_MANIFEST_NAME = "manifest.json"
_EXPECTED_FILES = frozenset({_DOSSIER_NAME, _MANIFEST_NAME})
_SHA256_HEX_LENGTH = 64
_EXPORTABLE_STATES = frozenset(
    {
        ResearchState.AWAITING_SUFFICIENCY,
        ResearchState.AWAITING_CANDIDATES,
        ResearchState.COMPLETED,
    }
)
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True, slots=True)
class DossierExportRequest:
    session_id: str
    output_directory: Path
    force: bool = False


@dataclass(frozen=True, slots=True)
class DossierExportResult:
    directory: Path
    manifest_sha256: str
    dossier_sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "directory": str(self.directory),
            "manifest_sha256": self.manifest_sha256,
            "dossier_sha256": self.dossier_sha256,
        }


def export_dossier(
    request: DossierExportRequest,
    *,
    store: ResearchStore,
    package_version: str,
) -> DossierExportResult:
    """Publish one validated, deterministic two-file dossier.

    This deliberately consumes only the public store reads named in the research
    export contract.  It never opens the corpus database or an acquisition file.
    """
    _validate_request(request, package_version)
    destination = _validate_destination(request.output_directory)

    session = store.get_session(request.session_id)
    if session.state not in _EXPORTABLE_STATES:
        raise ValueError("dossier export requires a completed or waiting session")
    assessment = store.get_latest_assessment(request.session_id)
    candidates = store.list_candidates(request.session_id)
    history = store.get_session_history(request.session_id)
    dossier_bytes = _render_dossier(session, assessment, candidates, history).encode("utf-8")
    dossier_sha256 = _sha256(dossier_bytes)
    manifest_bytes = _canonical_json(
        _manifest_payload(
            session=session,
            assessment=assessment,
            candidates=candidates,
            history=history,
            package_version=package_version,
            dossier_sha256=dossier_sha256,
        )
    )
    manifest_sha256 = _sha256(manifest_bytes)

    _publish(
        destination=destination,
        dossier_bytes=dossier_bytes,
        manifest_bytes=manifest_bytes,
        force=request.force,
    )
    return DossierExportResult(destination, manifest_sha256, dossier_sha256)


def _validate_request(request: DossierExportRequest, package_version: str) -> None:
    if not isinstance(request, DossierExportRequest):
        raise TypeError("request must be a DossierExportRequest")
    if not isinstance(request.session_id, str) or not request.session_id:
        raise ValueError("session ID must be non-empty")
    if not isinstance(request.output_directory, Path):
        raise TypeError("output directory must be a Path")
    if not isinstance(request.force, bool):
        raise TypeError("force must be a boolean")
    if not isinstance(package_version, str) or not package_version:
        raise ValueError("package version must be non-empty")


def _validate_destination(destination: Path) -> Path:
    if not destination.is_absolute():
        raise ValueError("output directory must be absolute")
    if ".." in destination.parts:
        raise ValueError("output directory must not contain path traversal")
    if destination.name in {"", ".", os.sep}:
        raise ValueError("output directory must name a directory")

    current = Path(destination.anchor)
    for part in destination.parts[1:-1]:
        current /= part
        _require_directory_component(current)
    parent = destination.parent
    _require_directory_component(parent)
    if _lstat(destination) is not None and destination.is_symlink():
        raise ValueError("output directory must not be a symlink")
    return destination


def _require_directory_component(path: Path) -> None:
    mode = _lstat(path)
    if mode is None:
        raise FileNotFoundError(f"output parent does not exist: {path}")
    if stat.S_ISLNK(mode.st_mode):
        raise ValueError("output directory must not contain a symlink")
    if not stat.S_ISDIR(mode.st_mode):
        raise NotADirectoryError(f"output parent is not a directory: {path}")


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


def _manifest_payload(
    *,
    session: ResearchSession,
    assessment: ResearchAssessment | None,
    candidates: tuple[ResearchCandidate, ...],
    history: SessionHistory,
    package_version: str,
    dossier_sha256: str,
) -> dict[str, object]:
    evidence = [] if assessment is None else [_passage_payload(item) for item in assessment.passages]
    return {
        "acquisition_outcomes": [_outcome_payload(item) for item in history.acquisition_outcomes],
        "assessment": None if assessment is None else _assessment_payload(assessment),
        "candidates": [_candidate_payload(item) for item in candidates],
        "coverage_limits": _coverage_limits(assessment, history),
        "decisions": [{"action": decision.action} for decision in history.decisions],
        "dossier_sha256": dossier_sha256,
        "evidence": evidence,
        "format_version": _FORMAT_VERSION,
        "package_version": package_version,
        "session": _session_payload(session),
    }


def _session_payload(session: ResearchSession) -> dict[str, object]:
    return {
        "created_at": _timestamp(session.created_at),
        "discovery_fingerprint": session.discovery_fingerprint,
        "freshness_profile": session.freshness_profile.value,
        "languages": list(session.languages),
        "queries": [query.text for query in session.queries],
        "revision": session.revision,
        "session_id": session.session_id,
        "state": session.state.value,
        "topic": session.topic,
        "updated_at": _timestamp(session.updated_at),
    }


def _assessment_payload(assessment: ResearchAssessment) -> dict[str, object]:
    return {
        "coverage": {
            "distinct_channels": assessment.coverage.distinct_channels,
            "matched_passages": assessment.coverage.matched_passages,
            "matched_videos": assessment.coverage.matched_videos,
            "newest_source_published_at": _date(assessment.coverage.newest_source_published_at),
            "queries_with_zero_hits": list(assessment.coverage.queries_with_zero_hits),
            "unknown_publication_date_count": assessment.coverage.unknown_publication_date_count,
        },
        "created_at": _timestamp(assessment.created_at),
        "freshness": {
            "last_successful_discovery_at": _timestamp(assessment.freshness.last_successful_discovery_at),
            "maximum_age_days": assessment.freshness.maximum_age_days,
            "profile": assessment.freshness.profile.value,
            "reason": assessment.freshness.reason,
            "stale": assessment.freshness.stale,
        },
        "snapshot": {
            "catalog_generation": assessment.snapshot.catalog_generation,
            "search_generation": assessment.snapshot.search_generation,
        },
    }


def _passage_payload(passage: PassageEvidence) -> dict[str, object]:
    return {
        "channel_id": passage.channel_id,
        "excerpt": passage.excerpt,
        "passage_id": passage.passage_id,
        "query": passage.query,
        "rank": passage.rank,
        "source_sha256": passage.source_sha256,
        "url": passage.url,
        "video_id": passage.video_id,
    }


def _candidate_payload(candidate: ResearchCandidate) -> dict[str, object]:
    return {
        "channel_id": candidate.channel_id,
        "channel_title": candidate.channel_title,
        "matched_queries": list(candidate.matched_queries),
        "original_rank": candidate.original_rank,
        "published_at": _date(candidate.published_at),
        "status": candidate.status.value,
        "title": candidate.title,
        "video_id": candidate.video_id,
        "watch_url": candidate.watch_url,
    }


def _outcome_payload(outcome: ResearchAcquisitionOutcome) -> dict[str, object]:
    return {
        "error_code": outcome.error_code,
        "source_sha256": outcome.source_sha256,
        "status": outcome.status.value,
        "video_id": outcome.video_id,
    }


def _coverage_limits(assessment: ResearchAssessment | None, history: SessionHistory) -> list[str]:
    limits: list[str] = []
    if assessment is None:
        limits.append("No stored assessment is available.")
    else:
        if not assessment.passages:
            limits.append("No source-backed passages were stored.")
        limits.extend(f"No stored hits for query: {query}" for query in assessment.coverage.queries_with_zero_hits)
        if assessment.freshness.stale:
            limits.append(f"Stored assessment is stale: {assessment.freshness.reason}.")
        if assessment.coverage.unknown_publication_date_count:
            limits.append(
                f"Publication date is unknown for {assessment.coverage.unknown_publication_date_count} stored source(s)."
            )
    for outcome in history.acquisition_outcomes:
        if outcome.status is not CandidateStatus.ACQUIRED:
            detail = "" if outcome.error_code is None else f" ({outcome.error_code})"
            limits.append(f"Acquisition for {outcome.video_id} ended as {outcome.status.value}{detail}.")
    return limits


def _render_dossier(
    session: ResearchSession,
    assessment: ResearchAssessment | None,
    candidates: tuple[ResearchCandidate, ...],
    history: SessionHistory,
) -> str:
    lines = [f"# {session.topic}", "", "## Research scope", ""]
    lines.extend(
        [
            f"- Session: `{session.session_id}`",
            f"- Queries: {_csv(query.text for query in session.queries)}",
            f"- Languages: {_csv(session.languages)}",
            f"- State: `{session.state.value}` at revision {session.revision}",
            "",
            "## Freshness and coverage",
            "",
        ]
    )
    if assessment is None:
        lines.append("- No stored assessment is available.")
    else:
        lines.extend(
            [
                f"- Assessment recorded: {_timestamp(assessment.created_at)}",
                f"- Freshness: `{assessment.freshness.reason}` ({assessment.freshness.profile.value})",
                f"- Stored matches: {assessment.coverage.matched_passages} passages, {assessment.coverage.matched_videos} videos, {assessment.coverage.distinct_channels} channels",
            ]
        )
    lines.extend(["", "## Source-backed evidence", ""])
    if assessment is None or not assessment.passages:
        lines.append("- No source-backed passages were stored.")
    else:
        for passage in assessment.passages:
            lines.extend(
                [
                    f"- [{passage.video_id}]({passage.url}) | query: {passage.query} | rank: {passage.rank} | source SHA-256: `{passage.source_sha256}`",
                    *[f"  > {line}" for line in passage.excerpt.splitlines()],
                ]
            )
    lines.extend(["", "## Newly acquired sources", ""])
    if not history.acquisition_outcomes:
        lines.append("- No stored acquisition outcomes.")
    else:
        for outcome in history.acquisition_outcomes:
            source_hash = "none" if outcome.source_sha256 is None else outcome.source_sha256
            error = "" if outcome.error_code is None else f" | error: `{outcome.error_code}`"
            lines.append(
                f"- `{outcome.video_id}` | status: `{outcome.status.value}` | source SHA-256: `{source_hash}`{error}"
            )
    lines.extend(["", "## Contradictions", "", "- No stored contradictions.", "", "## Coverage limits", ""])
    limits = _coverage_limits(assessment, history)
    lines.extend(f"- {limit}" for limit in limits) if limits else lines.append("- No stored coverage limits.")
    lines.extend(["", "## Unresolved questions", ""])
    unresolved = () if assessment is None else assessment.coverage.queries_with_zero_hits
    if unresolved:
        lines.extend(f"- {query}" for query in unresolved)
    else:
        lines.append("- No stored unresolved questions.")
    return "\n".join(lines) + "\n"


def _csv(values: Any) -> str:
    return ", ".join(values)


def _canonical_json(payload: dict[str, object]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _timestamp(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


def _date(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _publish(*, destination: Path, dossier_bytes: bytes, manifest_bytes: bytes, force: bool) -> None:
    parent_fd, parent_identity = _open_parent_directory(destination)
    stage_name: str | None = None
    stage_identity: tuple[int, int] | None = None
    try:
        initial = _destination_identity(parent_fd, destination.name)
        if initial is not None:
            if not force:
                raise FileExistsError("output directory already exists; use force to replace a validated prior dossier")
            _validate_prior_dossier(parent_fd, destination.name, initial)
        stage_name = f".{destination.name}.staging-{uuid.uuid4().hex}"
        os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        stage_identity = _destination_identity(parent_fd, stage_name)
        if stage_identity is None:
            raise RuntimeError("private staging directory disappeared")
        stage_fd = _open_named_directory(parent_fd, stage_name, stage_identity)
        try:
            _write_private_file(stage_fd, _DOSSIER_NAME, dossier_bytes)
            _write_private_file(stage_fd, _MANIFEST_NAME, manifest_bytes)
            _validate_staged_dossier(stage_fd, dossier_bytes, manifest_bytes)
            _fsync_directory(stage_fd)
        finally:
            os.close(stage_fd)
        _publish_stage(
            destination=destination,
            parent_fd=parent_fd,
            parent_identity=parent_identity,
            stage_name=stage_name,
            stage_identity=stage_identity,
            force=force,
            expected_identity=initial,
        )
    except BaseException:
        if stage_name is not None and stage_identity is not None:
            _remove_private_stage(parent_fd, stage_name, stage_identity)
        raise
    finally:
        os.close(parent_fd)


def _open_parent_directory(destination: Path) -> tuple[int, tuple[int, int]]:
    descriptor = os.open(destination.anchor, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in destination.parts[1:-1]:
            next_descriptor = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise NotADirectoryError("output parent is not a directory")
        return descriptor, (details.st_dev, details.st_ino)
    except BaseException:
        os.close(descriptor)
        raise


def _verify_parent_path(destination: Path, expected_identity: tuple[int, int]) -> None:
    try:
        descriptor, identity = _open_parent_directory(destination)
    except OSError as exc:
        raise ValueError("output parent changed during publication") from exc
    try:
        if identity != expected_identity:
            raise ValueError("output parent changed during publication")
    finally:
        os.close(descriptor)


def _destination_identity(parent_fd: int, name: str) -> tuple[int, int] | None:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(entry.st_mode):
        raise ValueError("output directory must not be a symlink")
    if not stat.S_ISDIR(entry.st_mode):
        raise FileExistsError("output path already exists and is not a directory")
    return (entry.st_dev, entry.st_ino)


def _open_named_directory(parent_fd: int, name: str, expected_identity: tuple[int, int]) -> int:
    current = _destination_identity(parent_fd, name)
    if current != expected_identity:
        raise ValueError("directory changed during publication")
    descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != expected_identity:
        os.close(descriptor)
        raise ValueError("directory changed during publication")
    return descriptor


def _require_directory_identity(parent_fd: int, name: str, expected_identity: tuple[int, int]) -> None:
    if _destination_identity(parent_fd, name) != expected_identity:
        raise ValueError("directory changed during publication")


def _write_private_file(directory_fd: int, name: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file(directory_fd: int, name: str) -> bytes:
    try:
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("dossier file is missing") from exc
    if stat.S_ISLNK(named.st_mode) or not stat.S_ISREG(named.st_mode):
        raise ValueError("dossier file is not regular")
    descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise ValueError("dossier file changed during validation")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65_536):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_staged_dossier(stage_fd: int, dossier_bytes: bytes, manifest_bytes: bytes) -> None:
    if _read_regular_file(stage_fd, _DOSSIER_NAME) != dossier_bytes:
        raise ValueError("staged dossier checksum mismatch")
    manifest_bytes_on_disk = _read_regular_file(stage_fd, _MANIFEST_NAME)
    parsed = json.loads(manifest_bytes_on_disk.decode("utf-8"))
    if not isinstance(parsed, dict) or _sha256(manifest_bytes) != _sha256(manifest_bytes_on_disk):
        raise ValueError("staged manifest checksum mismatch")
    if parsed.get("dossier_sha256") != _sha256(dossier_bytes):
        raise ValueError("staged dossier checksum is not recorded in its manifest")


def _publish_stage(
    *,
    destination: Path,
    parent_fd: int,
    parent_identity: tuple[int, int],
    stage_name: str,
    stage_identity: tuple[int, int],
    force: bool,
    expected_identity: tuple[int, int] | None,
) -> None:
    _verify_parent_path(destination, parent_identity)
    if _destination_identity(parent_fd, destination.name) != expected_identity:
        raise FileExistsError("destination changed during publication")
    _require_directory_identity(parent_fd, stage_name, stage_identity)
    if not force:
        try:
            os.rename(stage_name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except OSError as exc:
            if _destination_identity(parent_fd, destination.name) is not None:
                raise FileExistsError("destination changed during publication") from exc
            raise
        _verify_parent_path(destination, parent_identity)
        _require_directory_identity(parent_fd, destination.name, stage_identity)
        _fsync_directory(parent_fd)
        return

    if expected_identity is None:
        raise ValueError("force requires an existing validated prior dossier")
    _validate_prior_dossier(parent_fd, destination.name, expected_identity)
    backup_name = f".{destination.name}.backup-{uuid.uuid4().hex}"
    moved_prior = False
    try:
        _verify_parent_path(destination, parent_identity)
        _require_directory_identity(parent_fd, destination.name, expected_identity)
        _require_directory_identity(parent_fd, stage_name, stage_identity)
        os.rename(destination.name, backup_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        moved_prior = True
        _require_directory_identity(parent_fd, backup_name, expected_identity)
        _require_directory_identity(parent_fd, stage_name, stage_identity)
        os.rename(stage_name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _verify_parent_path(destination, parent_identity)
        _require_directory_identity(parent_fd, destination.name, stage_identity)
        _fsync_directory(parent_fd)
    except BaseException:
        if moved_prior and _destination_identity(parent_fd, destination.name) is None:
            _require_directory_identity(parent_fd, backup_name, expected_identity)
            os.rename(backup_name, destination.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        raise
    _remove_validated_prior(parent_fd, backup_name, expected_identity)


def _validate_prior_dossier(parent_fd: int, name: str, expected_identity: tuple[int, int]) -> None:
    try:
        directory_fd = _open_named_directory(parent_fd, name, expected_identity)
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError) as exc:
        raise ValueError("force requires a validated prior dossier") from exc
    try:
        if set(os.listdir(directory_fd)) != _EXPECTED_FILES:
            raise ValueError("force requires a validated prior dossier")
        try:
            manifest = json.loads(_read_regular_file(directory_fd, _MANIFEST_NAME).decode("utf-8"))
            dossier_bytes = _read_regular_file(directory_fd, _DOSSIER_NAME)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError("force requires a validated prior dossier") from exc
        if not _is_valid_manifest_shape(manifest):
            raise ValueError("force requires a validated prior dossier")
        checksum = manifest["dossier_sha256"]
        if not _is_sha256(checksum) or checksum != _sha256(dossier_bytes):
            raise ValueError("force requires a validated prior dossier")
    finally:
        os.close(directory_fd)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == _SHA256_HEX_LENGTH and all(character in "0123456789abcdef" for character in value)


def _is_valid_manifest_shape(manifest: object) -> bool:
    if not isinstance(manifest, dict):
        return False
    required = {
        "acquisition_outcomes",
        "assessment",
        "candidates",
        "coverage_limits",
        "decisions",
        "dossier_sha256",
        "evidence",
        "format_version",
        "package_version",
        "session",
    }
    return (
        set(manifest) == required
        and manifest["format_version"] == _FORMAT_VERSION
        and isinstance(manifest["package_version"], str)
        and isinstance(manifest["session"], dict)
        and (manifest["assessment"] is None or isinstance(manifest["assessment"], dict))
        and all(isinstance(manifest[name], list) for name in ("acquisition_outcomes", "candidates", "coverage_limits", "decisions", "evidence"))
    )


def _remove_private_stage(parent_fd: int, name: str, expected_identity: tuple[int, int]) -> None:
    if not name.startswith(".") or ".staging-" not in name:
        return
    try:
        stage_fd = _open_named_directory(parent_fd, name, expected_identity)
    except (FileNotFoundError, NotADirectoryError, ValueError, OSError):
        return
    try:
        for item_name in _EXPECTED_FILES:
            try:
                os.unlink(item_name, dir_fd=stage_fd)
            except FileNotFoundError:
                continue
    finally:
        os.close(stage_fd)
    try:
        _require_directory_identity(parent_fd, name, expected_identity)
        os.rmdir(name, dir_fd=parent_fd)
    except (FileNotFoundError, ValueError, OSError):
        return


def _remove_validated_prior(parent_fd: int, name: str, expected_identity: tuple[int, int]) -> None:
    _validate_prior_dossier(parent_fd, name, expected_identity)
    directory_fd = _open_named_directory(parent_fd, name, expected_identity)
    try:
        for item_name in _EXPECTED_FILES:
            os.unlink(item_name, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)
    _require_directory_identity(parent_fd, name, expected_identity)
    os.rmdir(name, dir_fd=parent_fd)


def _fsync_directory(directory_fd: int) -> None:
    os.fsync(directory_fd)
