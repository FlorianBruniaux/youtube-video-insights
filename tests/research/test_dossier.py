from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from yt_insights.research.dossier import (
    DossierExportRequest,
    export_dossier,
)
from yt_insights.research.models import (
    CandidateStatus,
    CoverageMetrics,
    DatabaseSnapshot,
    FreshnessAssessment,
    FreshnessProfile,
    PassageEvidence,
    QuerySpec,
    ResearchAcquisitionOutcome,
    ResearchAssessment,
    ResearchCandidate,
    ResearchState,
)
from yt_insights.research.store import ResearchStore

NOW = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
SESSION_ID = "01K4RESEARCH0000000000000000"
VIDEO_ID = "abc123DEF45"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def _store(tmp_path: Path) -> ResearchStore:
    return ResearchStore(tmp_path / "research.sqlite3", now=lambda: NOW)


def _completed_store(tmp_path: Path, *, excerpt: str = "Local inference keeps models on-device.") -> ResearchStore:
    store = _store(tmp_path)
    store.create_session(
        session_id=SESSION_ID,
        topic="Local AI inference",
        queries=(QuerySpec("local LLM inference"),),
        languages=("en",),
        freshness_profile=FreshnessProfile.FAST,
        discovery_fingerprint="a" * 64,
    )
    assessment = ResearchAssessment(
        created_at=NOW,
        snapshot=DatabaseSnapshot("search-1", "catalog-1"),
        coverage=CoverageMetrics(1, 1, 1, ("unanswered query",), date(2026, 8, 30), 0),
        freshness=FreshnessAssessment(FreshnessProfile.FAST, 14, None, False, "fresh"),
        passages=(
            PassageEvidence(
                query="local LLM inference",
                passage_id="passage-1",
                video_id=VIDEO_ID,
                channel_id="channel-1",
                rank=1,
                url=WATCH_URL,
                excerpt=excerpt,
                source_sha256="b" * 64,
            ),
        ),
        videos=(),
    )
    store.record_assessment(SESSION_ID, expected_revision=0, assessment=assessment)
    store.decide_sufficiency(SESSION_ID, expected_revision=1, sufficient=False, idempotency_key="refresh")
    candidate = ResearchCandidate(
        video_id=VIDEO_ID,
        title="Local inference",
        channel_id="channel-1",
        channel_title="Channel",
        published_at=date(2026, 8, 30),
        watch_url=WATCH_URL,
        matched_queries=("local LLM inference",),
        original_rank=1,
        status=CandidateStatus.CANDIDATE,
    )
    store.record_candidates(SESSION_ID, expected_revision=2, candidates=(candidate,), provider_name="provider", provider_version=1, errors=())
    store.approve_candidates(SESSION_ID, expected_revision=3, video_ids=(VIDEO_ID,), idempotency_key="approve")
    store.start_acquisition_attempt(SESSION_ID, expected_revision=4, video_ids=(VIDEO_ID,), idempotency_key="attempt-key", attempt_id="attempt-1")
    store.record_acquisition_batch(
        SESSION_ID,
        expected_revision=4,
        attempt_id="attempt-1",
        outcomes=(ResearchAcquisitionOutcome("attempt-1", VIDEO_ID, CandidateStatus.ACQUIRED, None, "c" * 64),),
    )
    store.complete_reindexing(SESSION_ID, expected_revision=5)
    store.record_assessment(SESSION_ID, expected_revision=6, assessment=assessment)
    store.decide_sufficiency(SESSION_ID, expected_revision=7, sufficient=True, idempotency_key="complete")
    return store


def _request(directory: Path, *, force: bool = False) -> DossierExportRequest:
    return DossierExportRequest(session_id=SESSION_ID, output_directory=directory, force=force)


def test_export_is_byte_identical_and_only_renders_stored_bounded_evidence(tmp_path: Path) -> None:
    """Changing serialization order, adding a clock, or reading corpus bodies breaks this export."""
    store = _completed_store(tmp_path)

    first = export_dossier(_request(tmp_path / "one"), store=store, package_version="1.2.3")
    second = export_dossier(_request(tmp_path / "two"), store=store, package_version="1.2.3")

    first_manifest = (first.directory / "manifest.json").read_bytes()
    first_dossier = (first.directory / "dossier.md").read_bytes()
    assert first_manifest == (second.directory / "manifest.json").read_bytes()
    assert first_dossier == (second.directory / "dossier.md").read_bytes()
    assert first.to_dict() == {
        "directory": str(tmp_path / "one"),
        "manifest_sha256": hashlib.sha256(first_manifest).hexdigest(),
        "dossier_sha256": hashlib.sha256(first_dossier).hexdigest(),
    }
    manifest = json.loads(first_manifest)
    rendered = first_dossier.decode("utf-8")
    assert manifest["format_version"] == 1
    assert manifest["package_version"] == "1.2.3"
    assert manifest["dossier_sha256"] == hashlib.sha256(first_dossier).hexdigest()
    assert manifest["evidence"][0]["excerpt"] == "Local inference keeps models on-device."
    assert f"evidence: `{manifest['evidence'][0]['passage_id']}`" in rendered
    assert manifest["acquisition_outcomes"] == [
        {
            "error_code": None,
            "source_sha256": "c" * 64,
            "status": "acquired",
            "video_id": VIDEO_ID,
        }
    ]
    assert manifest["decisions"] == [
        {"action": "refresh"},
        {"action": "approve_candidates"},
        {"action": "sufficient"},
    ]
    assert "## Research scope" in rendered
    assert "## Freshness and coverage" in rendered
    assert "## Source-backed evidence" in rendered
    assert "## Newly acquired sources" in rendered
    assert "## Contradictions" in rendered
    assert "## Coverage limits" in rendered
    assert "## Unresolved questions" in rendered
    assert str(tmp_path) not in first_manifest.decode("utf-8")
    assert "research.sqlite3" not in first_manifest.decode("utf-8")
    assert "SELECT " not in first_manifest.decode("utf-8")
    assert "2026-08-31T10:00:00" in first_manifest.decode("utf-8")


def test_export_never_synthesizes_missing_assessment_evidence(tmp_path: Path) -> None:
    """Inventing passages for a session without an assessment must remain impossible."""
    store = _store(tmp_path)
    store.create_session(
        session_id=SESSION_ID,
        topic="No assessment",
        queries=(QuerySpec("missing evidence"),),
        languages=("fr",),
        freshness_profile=FreshnessProfile.HISTORICAL,
        discovery_fingerprint="d" * 64,
    )

    created = store.get_session(SESSION_ID)

    class WaitingStore:
        def get_session(self, session_id: str) -> object:
            assert session_id == SESSION_ID
            return replace(created, state=ResearchState.AWAITING_SUFFICIENCY)

        def get_latest_assessment(self, session_id: str) -> object:
            return store.get_latest_assessment(session_id)

        def list_candidates(self, session_id: str) -> object:
            return store.list_candidates(session_id)

        def get_session_history(self, session_id: str) -> object:
            return store.get_session_history(session_id)

    result = export_dossier(_request(tmp_path / "dossier"), store=WaitingStore(), package_version="1.2.3")  # type: ignore[arg-type]

    manifest = json.loads((result.directory / "manifest.json").read_text())
    rendered = (result.directory / "dossier.md").read_text()
    assert manifest["assessment"] is None
    assert manifest["evidence"] == []
    assert "No stored assessment is available." in rendered
    assert "No source-backed passages were stored." in rendered


@pytest.mark.parametrize(
    "state",
    (
        ResearchState.ASSESSING,
        ResearchState.DISCOVERING,
        ResearchState.ACQUIRING,
        ResearchState.REINDEXING,
        ResearchState.FAILED_RETRYABLE,
        ResearchState.CANCELLED,
    ),
)
def test_export_rejects_sessions_that_are_not_waiting_or_completed(tmp_path: Path, state: ResearchState) -> None:
    """An active, failed, or cancelled workflow has no stable export contract."""
    store = _completed_store(tmp_path)
    completed = store.get_session(SESSION_ID)

    class StateStore:
        def get_session(self, session_id: str) -> object:
            assert session_id == SESSION_ID
            return replace(completed, state=state)

        def get_latest_assessment(self, session_id: str) -> object:
            return store.get_latest_assessment(session_id)

        def list_candidates(self, session_id: str) -> object:
            return store.list_candidates(session_id)

        def get_session_history(self, session_id: str) -> object:
            return store.get_session_history(session_id)

    target = tmp_path / state.value
    with pytest.raises(ValueError, match="completed or waiting"):
        export_dossier(_request(target), store=StateStore(), package_version="1.2.3")  # type: ignore[arg-type]
    assert not target.exists()


def test_export_quotes_every_line_of_a_stored_excerpt(tmp_path: Path) -> None:
    """Stored text must not be able to inject a Markdown section heading."""
    store = _completed_store(tmp_path, excerpt="First line\n## injected")

    result = export_dossier(_request(tmp_path / "dossier"), store=store, package_version="1.2.3")

    rendered = (result.directory / "dossier.md").read_text()
    assert "  > First line\n  > ## injected\n" in rendered
    assert "\n## injected\n" not in rendered


def test_existing_output_requires_force_and_force_requires_valid_prior_dossier(tmp_path: Path) -> None:
    """Replacing arbitrary user directories rather than an identified prior dossier must fail."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    export_dossier(_request(target), store=store, package_version="1.2.3")

    with pytest.raises(FileExistsError, match="force"):
        export_dossier(_request(target), store=store, package_version="1.2.3")
    export_dossier(_request(target, force=True), store=store, package_version="1.2.4")
    (target / "extra.txt").write_text("user file")
    with pytest.raises(ValueError, match="validated prior dossier"):
        export_dossier(_request(target, force=True), store=store, package_version="1.2.5")
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "dossier.md").write_text("not our dossier")
    (invalid / "manifest.json").write_text(
        json.dumps({"dossier_sha256": hashlib.sha256(b"not our dossier").hexdigest()})
    )
    with pytest.raises(ValueError, match="validated prior dossier"):
        export_dossier(_request(invalid, force=True), store=store, package_version="1.2.5")


def test_export_rejects_unsafe_destination_shapes_and_allows_another_root(tmp_path: Path) -> None:
    """Following symlinks, accepting traversal, or assuming the project root would make publication unsafe."""
    store = _completed_store(tmp_path)
    external_root = tmp_path.parent / f"external-{tmp_path.name}"
    external_root.mkdir()
    target = external_root / "copied-dossier"

    result = export_dossier(_request(target), store=store, package_version="1.2.3")

    assert result.directory == target
    with pytest.raises(ValueError, match="absolute"):
        export_dossier(_request(Path("relative") / ".." / "escape"), store=store, package_version="1.2.3")
    with pytest.raises(ValueError, match="traversal"):
        export_dossier(_request(tmp_path / "nested" / ".." / "escape"), store=store, package_version="1.2.3")
    not_a_directory = tmp_path / "file-parent"
    not_a_directory.write_text("file")
    with pytest.raises(NotADirectoryError):
        export_dossier(_request(not_a_directory / "dossier"), store=store, package_version="1.2.3")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        export_dossier(_request(link), store=store, package_version="1.2.3")


def test_topic_directory_creation_rejects_an_intermediate_symlink_without_writing(
    tmp_path: Path,
) -> None:
    """Following an intermediate configured-root symlink would redirect a write."""
    import yt_insights.research.dossier as dossier

    redirect_target = tmp_path / "PRIVATE-INTERMEDIATE-CANARY"
    redirect_target.mkdir()
    configured_parent = tmp_path / "configured"
    configured_parent.mkdir()
    (configured_parent / "linked").symlink_to(
        redirect_target,
        target_is_directory=True,
    )
    configured_root = configured_parent / "linked" / "research"
    (redirect_target / "research").mkdir()

    with pytest.raises(OSError):
        dossier.ensure_dossier_topic_directory(configured_root, "local-ai")

    assert list((redirect_target / "research").iterdir()) == []


def test_topic_directory_creation_rejects_root_replacement_without_redirecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root swap before mkdir must neither redirect nor retain the owned directory."""
    import yt_insights.research.dossier as dossier

    configured_root = tmp_path / "research"
    configured_root.mkdir()
    moved_root = tmp_path / "moved-research"
    replacement_root = tmp_path / "replacement-research"
    replacement_root.mkdir()
    original_mkdir = dossier.os.mkdir
    original_rename = dossier.os.rename
    swapped = False

    def swap_root_before_creation(
        name: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        nonlocal swapped
        if not swapped and name == "local-ai" and kwargs.get("dir_fd") is not None:
            swapped = True
            original_rename(configured_root, moved_root)
            original_rename(replacement_root, configured_root)
        return original_mkdir(name, *args, **kwargs)

    monkeypatch.setattr(dossier.os, "mkdir", swap_root_before_creation)

    with pytest.raises(ValueError, match="parent changed"):
        dossier.ensure_dossier_topic_directory(configured_root, "local-ai")

    assert not (configured_root / "local-ai").exists()
    assert (moved_root / "local-ai").is_dir()
    assert list((moved_root / "local-ai").iterdir()) == []


def test_topic_directory_failure_never_attempts_rmdir_after_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retaining an empty owned directory avoids deleting a substituted foreign inode."""
    import yt_insights.research.dossier as dossier

    configured_root = tmp_path / "research"
    configured_root.mkdir()
    original_verify = dossier._verify_parent_path
    original_rmdir = dossier.os.rmdir
    original_rename = dossier.os.rename
    original_mkdir = dossier.os.mkdir
    verify_calls = 0
    rmdir_calls: list[str] = []

    def fail_after_creation(
        destination: Path,
        identity: tuple[int, int],
    ) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 3:
            raise ValueError("injected post-creation failure")
        original_verify(destination, identity)

    def substitute_before_rmdir(
        name: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        if name == "local-ai" and kwargs.get("dir_fd") is not None:
            rmdir_calls.append(str(name))
            directory_fd = kwargs["dir_fd"]
            original_rename(
                name,
                "owned-retained",
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            original_mkdir(name, mode=0o755, dir_fd=directory_fd)
        return original_rmdir(name, *args, **kwargs)

    monkeypatch.setattr(dossier, "_verify_parent_path", fail_after_creation)
    monkeypatch.setattr(dossier.os, "rmdir", substitute_before_rmdir)

    with pytest.raises(ValueError, match="injected post-creation failure"):
        dossier.ensure_dossier_topic_directory(configured_root, "local-ai")

    assert rmdir_calls == []
    assert (configured_root / "local-ai").is_dir()
    assert list((configured_root / "local-ai").iterdir()) == []


def test_export_preserves_destination_when_publication_is_interrupted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed directory replacement must leave no partially published dossier."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    import yt_insights.research.dossier as dossier

    original_rename = dossier.os.rename

    def fail_stage_publish(source: object, destination: object, *args: object, **kwargs: object) -> object:
        if destination == target.name:
            raise OSError("interrupted publication")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(dossier.os, "rename", fail_stage_publish)

    with pytest.raises(OSError, match="interrupted publication"):
        export_dossier(_request(target), store=store, package_version="1.2.3")

    assert not target.exists()
    assert not list(tmp_path.glob(".dossier.staging-*"))


def test_export_refuses_a_destination_created_during_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path swap after validation must not allow an unforced overwrite."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    import yt_insights.research.dossier as dossier

    original_rename = dossier.os.rename

    def swap_destination(source: object, destination: object, *args: object, **kwargs: object) -> object:
        if destination == target.name:
            target.mkdir()
            (target / "intruder.txt").write_text("do not replace")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(dossier.os, "rename", swap_destination)

    with pytest.raises(FileExistsError, match="destination changed"):
        export_dossier(_request(target), store=store, package_version="1.2.3")

    assert (target / "intruder.txt").read_text() == "do not replace"


def test_export_rejects_a_parent_swapped_after_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent pathname swap must neither redirect nor retain publication."""
    store = _completed_store(tmp_path)
    parent = tmp_path / "parent"
    parent.mkdir()
    moved_parent = tmp_path / "moved-parent"
    external = tmp_path / "external"
    external.mkdir()
    target = parent / "dossier"
    import yt_insights.research.dossier as dossier

    original_mkdir = dossier.os.mkdir
    original_rename = dossier.os.rename
    swapped = False

    def swap_parent(path: object, *args: object, **kwargs: object) -> object:
        nonlocal swapped
        if not swapped and Path(path).name.startswith(".dossier.staging-"):
            swapped = True
            original_rename(parent, moved_parent)
            parent.symlink_to(external, target_is_directory=True)
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(dossier.os, "mkdir", swap_parent)

    with pytest.raises(ValueError, match="parent changed"):
        export_dossier(_request(target), store=store, package_version="1.2.3")

    assert not (external / "dossier").exists()
    assert not (moved_parent / "dossier").exists()


def test_export_removes_unforced_dossier_when_final_parent_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-rename failure must not leave an unforced new dossier behind."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    import yt_insights.research.dossier as dossier

    original_verify = dossier._verify_parent_path
    verify_calls = 0

    def fail_final_parent_check(destination: Path, identity: tuple[int, int]) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise ValueError("injected final parent check failure")
        original_verify(destination, identity)

    monkeypatch.setattr(dossier, "_verify_parent_path", fail_final_parent_check)

    with pytest.raises(ValueError, match="injected final parent check failure"):
        export_dossier(_request(target), store=store, package_version="1.2.3")

    assert not target.exists()
    assert not list(tmp_path.glob(".dossier.staging-*"))


def test_export_restores_forced_dossier_when_final_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-rename forced export must restore the exact validated prior dossier."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    export_dossier(_request(target), store=store, package_version="1.2.3")
    previous = {name: (target / name).read_bytes() for name in ("manifest.json", "dossier.md")}
    import yt_insights.research.dossier as dossier

    original_fsync = dossier._fsync_directory
    fsync_calls = 0

    def fail_final_fsync(directory_fd: int) -> None:
        nonlocal fsync_calls
        fsync_calls += 1
        if fsync_calls >= 2:
            raise OSError("injected final fsync failure")
        original_fsync(directory_fd)

    monkeypatch.setattr(dossier, "_fsync_directory", fail_final_fsync)

    with pytest.raises(OSError, match="injected final fsync failure"):
        export_dossier(_request(target, force=True), store=store, package_version="1.2.4")

    assert {name: (target / name).read_bytes() for name in previous} == previous
    assert not list(tmp_path.glob(".dossier.staging-*"))
    assert not list(tmp_path.glob(".dossier.backup-*"))


def test_export_keeps_committed_replacement_when_backup_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backup cleanup occurs after commit and cannot turn a successful export into failure."""
    store = _completed_store(tmp_path)
    target = tmp_path / "dossier"
    export_dossier(_request(target), store=store, package_version="1.2.3")
    previous = {name: (target / name).read_bytes() for name in ("manifest.json", "dossier.md")}
    import yt_insights.research.dossier as dossier

    def fail_cleanup(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
        raise OSError("injected backup cleanup failure")

    monkeypatch.setattr(dossier, "_remove_validated_prior", fail_cleanup)

    result = export_dossier(_request(target, force=True), store=store, package_version="1.2.4")

    manifest = json.loads((result.directory / "manifest.json").read_text())
    tombstones = list(tmp_path.glob(".dossier.backup-*.cleanup-*"))
    assert manifest["package_version"] == "1.2.4"
    assert result.directory == target
    assert len(tombstones) == 1
    assert {name: (tombstones[0] / name).read_bytes() for name in previous} == previous
