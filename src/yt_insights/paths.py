"""Immutable filesystem paths derived from one corpus data root."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataPaths:
    """Resolved locations for all corpus artifacts and local databases."""

    root: Path
    transcripts: Path
    insights: Path
    shorts: Path
    clips: Path
    exports: Path
    catalog_database: Path
    search_database: Path

    @classmethod
    def from_root(cls, root: Path) -> "DataPaths":
        """Derive every location from a root without requiring it to exist."""
        resolved = root.expanduser().resolve(strict=False)
        return cls(
            root=resolved,
            transcripts=resolved / "transcripts",
            insights=resolved / "insights",
            shorts=resolved / "shorts",
            clips=resolved / "clips",
            exports=resolved / "exports",
            catalog_database=resolved / "catalog.sqlite3",
            search_database=resolved / ".search" / "search-v1.sqlite3",
        )
