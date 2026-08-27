"""Safe translation of user text to an FTS5 MATCH expression."""

from __future__ import annotations

import re


_TOKENS = re.compile(r"\w+", re.UNICODE)
_MAX_QUERY_CODEPOINTS = 500


class InvalidSearchQuery(ValueError):
    """Raised when text cannot form a safe search query."""


def build_fts_expression(text: str) -> str:
    """Return a literal-only FTS5 expression for user-provided text."""
    if not isinstance(text, str) or len(text) > _MAX_QUERY_CODEPOINTS:
        raise InvalidSearchQuery("search text is invalid")
    tokens = _TOKENS.findall(text)
    if not tokens:
        raise InvalidSearchQuery("search text has no searchable terms")
    return " AND ".join(f'"{token}"' for token in tokens)
