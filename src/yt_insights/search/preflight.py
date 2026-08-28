"""Capacity checks for safe local search-index rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .corpus import _stable_file_size, discover_corpus_sources


_MINIMUM_FREE_BYTES = 256 * 1024**2


class IndexSpacePreflightError(RuntimeError):
    """Base error raised when index capacity cannot be established safely."""


class InsufficientIndexSpace(IndexSpacePreflightError):
    """Raised when an atomic index rebuild cannot fit on the target filesystem."""


@dataclass(frozen=True, slots=True)
class IndexSpacePreflightReport:
    corpus_root: Path
    database_path: Path
    disk_usage_path: Path
    source_files: int
    source_bytes: int
    required_bytes: int
    available_bytes: int
    sources_discovered: int = 0
    sources_excluded: int = 0


def _nearest_existing_directory(path: Path) -> Path:
    candidate = path.parent
    while not candidate.exists() or not candidate.is_dir():
        parent = candidate.parent
        if parent == candidate:
            raise IndexSpacePreflightError(
                f"no existing directory is available for database path {path}"
            )
        candidate = parent
    return candidate


def _source_inventory(corpus_root: Path) -> tuple[int, int, int]:
    candidates = discover_corpus_sources(corpus_root)
    source_files = 0
    source_bytes = 0
    for candidate in candidates:
        if candidate.invalid_reason in {
            "outside_corpus_root",
            "symlink_source",
            "non_regular_source",
        }:
            continue
        if candidate.invalid_reason == "source_unavailable":
            raise IndexSpacePreflightError(
                f"cannot inventory transcript source {candidate.source_relpath}"
            )
        try:
            source_size = _stable_file_size(candidate)
        except OSError as error:
            raise IndexSpacePreflightError(
                f"cannot inventory transcript source {candidate.source_relpath}"
            ) from error
        source_files += 1
        source_bytes += source_size
    return len(candidates), source_files, source_bytes


def preflight_index_space(corpus_root: Path, database_path: Path) -> IndexSpacePreflightReport:
    """Ensure the target filesystem can hold an atomic rebuild of the corpus index."""
    root = Path(corpus_root)
    if not root.is_dir():
        raise ValueError("corpus_root must be an existing directory")
    database = Path(database_path)
    sources_discovered, source_files, source_bytes = _source_inventory(root)
    required_bytes = max(_MINIMUM_FREE_BYTES, 2 * source_bytes)
    disk_usage_path = _nearest_existing_directory(database)
    available_bytes = shutil.disk_usage(disk_usage_path).free
    report = IndexSpacePreflightReport(
        corpus_root=root,
        database_path=database,
        disk_usage_path=disk_usage_path,
        sources_discovered=sources_discovered,
        source_files=source_files,
        sources_excluded=sources_discovered - source_files,
        source_bytes=source_bytes,
        required_bytes=required_bytes,
        available_bytes=available_bytes,
    )
    if available_bytes < required_bytes:
        raise InsufficientIndexSpace(
            "insufficient free disk space for search-index rebuild: "
            f"available {available_bytes} bytes, required {required_bytes} bytes "
            f"at {disk_usage_path}"
        )
    return report
