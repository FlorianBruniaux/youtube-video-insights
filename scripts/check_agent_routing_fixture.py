#!/usr/bin/env python3
"""Validate the offline fixture used to review agent routing boundaries."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


EXPECTED_MINIMUMS = {
    "youtube-acquire": 10,
    "youtube-research": 10,
    "youtube-export": 10,
    "none": 15,
}


class FixtureError(ValueError):
    """Raised when the routing fixture violates its validation contract."""


def _decode_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise FixtureError("fixture must be valid UTF-8") from error

    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise FixtureError("fixture must contain valid JSON") from error


def validate_fixture(raw: bytes) -> dict[str, Any]:
    payload = _decode_json(raw)
    if not isinstance(payload, list):
        raise FixtureError("schema error: top-level value must be a JSON array")

    counts: Counter[str] = Counter()
    seen_prompts: set[str] = set()
    for index, row in enumerate(payload):
        if not isinstance(row, dict) or set(row) != {"prompt", "expected"}:
            raise FixtureError(
                f"schema error: item {index} must contain only prompt and expected"
            )

        prompt = row["prompt"]
        expected = row["expected"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise FixtureError(f"schema error: item {index} prompt must be non-empty text")
        if not isinstance(expected, str):
            raise FixtureError(f"schema error: item {index} expected must be text")
        if expected not in EXPECTED_MINIMUMS:
            raise FixtureError(f"unknown expected label at item {index}: {expected!r}")

        if prompt in seen_prompts:
            raise FixtureError(f"duplicate prompt at item {index}")
        seen_prompts.add(prompt)
        counts[expected] += 1

    for label, minimum in EXPECTED_MINIMUMS.items():
        if counts[label] < minimum:
            raise FixtureError(
                f"expected at least {minimum} prompts for {label}, found {counts[label]}"
            )

    return {
        "counts": {label: counts[label] for label in sorted(EXPECTED_MINIMUMS)},
        "status": "ok",
        "total": len(payload),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the deterministic yt-insights agent routing fixture."
    )
    parser.add_argument("fixture", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        raw = args.fixture.read_bytes()
        summary = validate_fixture(raw)
    except (OSError, FixtureError) as error:
        print(f"routing fixture invalid: {error}", file=sys.stderr)
        return 1

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
