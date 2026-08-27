"""Small application-facing facade for local transcript search."""

from __future__ import annotations

from .models import SearchHit, SearchQuery
from .sqlite_fts import SQLiteFtsIndex


class SearchService:
    """Delegate search requests to the configured local index."""

    def __init__(self, index: SQLiteFtsIndex) -> None:
        self._index = index

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Return search hits from the configured index."""
        return self._index.search(query)
