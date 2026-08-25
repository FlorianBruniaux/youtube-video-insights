from __future__ import annotations

import json
from pathlib import Path

from conftest import FakeBackend
from yt_insights.analyzer import VideoInsight
from yt_insights.config import Config
from yt_insights.reporter import generate_report, top_tools


def test_generate_report_writes_the_aggregate_markdown_json_and_full_report(
    tmp_path: Path,
) -> None:
    """Detects a regression in aggregate report content or its output file contract."""
    report_path = tmp_path / "AGGREGATE_REPORT.md"
    insights = [
        VideoInsight(
            title="First video",
            vtt_path=tmp_path / "first.vtt",
            insight_path=tmp_path / "first.json",
            subject="Reliable delivery",
            key_points=["Measure outcomes"],
            tools=[{"name": "Codex", "context": "Review"}, "Git"],
            advice=["Ship a narrow slice"],
            quotes=["Make it observable."],
        ),
        VideoInsight(
            title="Second video",
            vtt_path=tmp_path / "second.vtt",
            insight_path=tmp_path / "second.json",
            subject="Testing discipline",
            key_points=["Keep a safety net"],
            tools=[{"name": "Codex", "context": "Test"}, {"name": "Git", "context": "History"}],
            advice=["Review regressions"],
            quotes=[],
        ),
    ]
    backend = FakeBackend([("A focused narrative.", "end_turn")])

    generate_report(insights, backend, Config(max_tokens=321), report_path=report_path)

    assert top_tools(insights) == [("Codex", 2), ("Git", 2)]
    assert report_path.read_text(encoding="utf-8") == (
        "# Rapport agrégé : 2 vidéos\n\n"
        "## Stack et outils cités\n\n"
        "- **Codex** (2)\n"
        "- **Git** (2)\n\n"
        "---\n\n"
        "A focused narrative.\n"
    )
    assert json.loads(report_path.with_suffix(".json").read_text(encoding="utf-8")) == {
        "video_count": 2,
        "top_tools": [{"name": "Codex", "count": 2}, {"name": "Git", "count": 2}],
        "videos": [
            {"title": "First video", "subject": "Reliable delivery", "tools": ["Codex", "Git"]},
            {"title": "Second video", "subject": "Testing discipline", "tools": ["Codex", "Git"]},
        ],
    }
    assert (tmp_path / "FULL_REPORT.md").read_text(encoding="utf-8") == (
        "# Rapport complet : 2 vidéo(s)\n\n"
        "---\n\n"
        "## First video\n\n\n"
        "**Sujet** : Reliable delivery\n\n\n"
        "**Points clés** :\n"
        "- Measure outcomes\n\n\n"
        "**Outils** :\n"
        "| Outil | Contexte |\n"
        "|---|---|\n"
        "| Codex | Review |\n"
        "| Git |  |\n\n\n"
        "**Conseils** :\n"
        "- Ship a narrow slice\n\n\n"
        "**Citations** :\n"
        "> \"Make it observable.\"\n\n"
        "---\n\n"
        "## Second video\n\n\n"
        "**Sujet** : Testing discipline\n\n\n"
        "**Points clés** :\n"
        "- Keep a safety net\n\n\n"
        "**Outils** :\n"
        "| Outil | Contexte |\n"
        "|---|---|\n"
        "| Codex | Test |\n"
        "| Git | History |\n\n\n"
        "**Conseils** :\n"
        "- Review regressions\n\n"
        "---\n\n"
        "# Synthèse agrégée\n\n"
        "## Stack et outils cités\n\n"
        "- **Codex** (2)\n"
        "- **Git** (2)\n\n"
        "A focused narrative.\n"
    )
    assert backend.calls[0][1:] == (321, 300)
