"""Small application-facing facade for local transcript search."""

from __future__ import annotations

from .models import DocumentRef, Passage, SearchHit, SearchQuery
from .sqlite_fts import SQLiteFtsIndex


class SearchService:
    """Delegate search requests to the configured local index."""

    def __init__(self, index: SQLiteFtsIndex) -> None:
        self._index = index

    def search(self, query: SearchQuery) -> tuple[SearchHit, ...]:
        """Return search hits from the configured index."""
        return self._index.search(query)

    def get_passage(self, passage_id: str) -> tuple[DocumentRef, Passage]:
        """Return one validated passage and its source document."""
        return self._index.get_passage(passage_id)
