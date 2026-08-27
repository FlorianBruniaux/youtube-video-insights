from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("retrieval systems", '"retrieval" AND "systems"'),
        ('quotes " and hyphen-terms: NEAR OR *', '"quotes" AND "and" AND "hyphen" AND "terms" AND "NEAR" AND "OR"'),
        ("Caf\u00e9 na\u00efve \u6771\u4eac", '"Caf\u00e9" AND "na\u00efve" AND "\u6771\u4eac"'),
    ],
)
def test_build_fts_expression_quotes_unicode_tokens_as_literals(text: str, expected: str) -> None:
    from yt_insights.search.query import build_fts_expression

    assert build_fts_expression(text) == expected


@pytest.mark.parametrize("text", ["", " - : * \" () ", "x" * 501])
def test_build_fts_expression_rejects_empty_or_oversized_input(text: str) -> None:
    from yt_insights.search.query import InvalidSearchQuery, build_fts_expression

    with pytest.raises(InvalidSearchQuery):
        build_fts_expression(text)
