#!/usr/bin/env python3
"""Measure warm local FTS search latency without emitting query content."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from math import ceil
from pathlib import Path
import statistics
import sys
import time

from yt_insights.search.models import SearchQuery
from yt_insights.search.sqlite_fts import SearchIndexError, SQLiteFtsIndex


DEFAULT_QUERIES = (
    "retrieval",
    "prompt engineering",
    "artificial intelligence",
    "machine learning",
    "software architecture",
    "developer tools",
    "content strategy",
    "open source",
    "automation",
    "product management",
)
MAX_QUERY_FILE_BYTES = 65_536
MAX_QUERIES = 100
MAX_QUERY_CODEPOINTS = 500


def nearest_rank_percentile(values: Sequence[float], quantile: float) -> float:
    """Return a percentile with the nearest-rank definition."""
    if not values:
        raise ValueError("percentile values must not be empty")
    if not 0 < quantile <= 1:
        raise ValueError("quantile must be greater than 0 and at most 1")
    ordered = sorted(values)
    return ordered[ceil(quantile * len(ordered)) - 1]


def _bounded_integer(option: str, minimum: int, maximum: int):
    def parse(value: str) -> int:
        try:
            parsed = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"{option} must be between {minimum} and {maximum}"
            ) from error
        if not minimum <= parsed <= maximum:
            raise argparse.ArgumentTypeError(
                f"{option} must be between {minimum} and {maximum}"
            )
        return parsed

    return parse


def _load_queries(queries_file: Path | None) -> tuple[str, ...]:
    if queries_file is None:
        return DEFAULT_QUERIES
    if queries_file.stat().st_size > MAX_QUERY_FILE_BYTES:
        raise ValueError(f"queries file must not exceed {MAX_QUERY_FILE_BYTES} bytes")
    queries = tuple(
        line.strip()
        for line in queries_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not queries:
        raise ValueError("queries file must contain at least one non-blank line")
    if len(queries) > MAX_QUERIES:
        raise ValueError(f"queries file must not contain more than {MAX_QUERIES} queries")
    if any(len(query) > MAX_QUERY_CODEPOINTS for query in queries):
        raise ValueError(
            f"each query must not exceed {MAX_QUERY_CODEPOINTS} Unicode code points"
        )
    return queries


def run_benchmark(
    *,
    database: Path,
    queries: Sequence[str],
    warmup: int,
    repeats: int,
    limit: int,
) -> dict[str, object]:
    """Run warmups and timed searches against one validated local index."""
    index = SQLiteFtsIndex(database)
    index.status()
    search_queries = tuple(SearchQuery(text=query, limit=limit) for query in queries)
    for query in search_queries:
        for _ in range(warmup):
            index.search(query)

    elapsed_ms: list[float] = []
    hit_counts: list[int] = []
    for query in search_queries:
        query_hit_counts: list[int] = []
        for _ in range(repeats):
            started = time.monotonic_ns()
            hits = index.search(query)
            elapsed_ms.append((time.monotonic_ns() - started) / 1_000_000)
            query_hit_counts.append(len(hits))
        if len(set(query_hit_counts)) != 1:
            raise RuntimeError("hit count changed between benchmark repetitions")
        hit_counts.append(query_hit_counts[0])

    latency = {
        "count": len(elapsed_ms),
        "min": round(min(elapsed_ms), 6),
        "median": round(statistics.median(elapsed_ms), 6),
        "p95": round(nearest_rank_percentile(elapsed_ms, 0.95), 6),
        "max": round(max(elapsed_ms), 6),
    }
    return {
        "schema_version": 1,
        "database": str(database.resolve()),
        "query_count": len(search_queries),
        "warmup_per_query": warmup,
        "repeats_per_query": repeats,
        "limit": limit,
        "latency_ms": latency,
        "hit_counts": hit_counts,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure warm local FTS search latency and emit JSON."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--queries-file", type=Path)
    parser.add_argument(
        "--warmup",
        type=_bounded_integer("--warmup", 0, 100),
        default=1,
        help="warmup runs per query, from 0 to 100 (default: 1)",
    )
    parser.add_argument(
        "--repeats",
        type=_bounded_integer("--repeats", 1, 1000),
        default=20,
        help="timed runs per query, from 1 to 1000 (default: 20)",
    )
    parser.add_argument(
        "--limit",
        type=_bounded_integer("--limit", 1, 20),
        default=10,
        help="maximum hits per query, from 1 to 20 (default: 10)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        queries = _load_queries(arguments.queries_file)
        payload = run_benchmark(
            database=arguments.database,
            queries=queries,
            warmup=arguments.warmup,
            repeats=arguments.repeats,
            limit=arguments.limit,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, SearchIndexError) as error:
        print(f"benchmark error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
