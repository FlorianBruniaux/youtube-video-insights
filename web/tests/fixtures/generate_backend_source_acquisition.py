"""Regenerate and verify the frontend fixture from the Python projection."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from yt_insights.acquisition import (
    AcquisitionItemReport,
    AcquisitionItemStatus,
    AcquisitionReport,
)
from yt_insights.web.api import _safe_acquisition_report


def _serialized_fixture() -> str:
    report = AcquisitionReport(
        selected=1,
        transcripts_ready=1,
        insights_ready=0,
        failures=(),
        items=(
            AcquisitionItemReport(
                video_id="abc123DEF45",
                status=AcquisitionItemStatus.ACQUIRED,
                source_sha256="b" * 64,
            ),
        ),
    )
    return json.dumps(
        _safe_acquisition_report(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def main() -> int:
    generated = _serialized_fixture()
    fixture = Path(__file__).with_name("backend-source-acquisition.json")
    if sys.argv[1:] == ["--check"]:
        return 0 if fixture.read_text(encoding="utf-8") == generated else 1
    if sys.argv[1:]:
        raise SystemExit("usage: generate_backend_source_acquisition.py [--check]")
    sys.stdout.write(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
