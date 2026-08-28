from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from yt_insights.doctor import CheckResult, DoctorReport


def test_doctor_json_is_deterministic_and_exits_zero_for_warnings(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import cli_doctor

    report = DoctorReport(
        data_root=str(tmp_path.resolve()),
        checks=(
            CheckResult("yt-dlp", "pass", "available"),
            CheckResult("ffmpeg", "warn", "not available"),
        ),
    )
    calls: list[bool] = []

    def fake_inspect(config, *, probe_backends: bool = False) -> DoctorReport:
        calls.append(probe_backends)
        return report

    monkeypatch.setattr(cli_doctor, "inspect_runtime", fake_inspect)

    result = CliRunner().invoke(cli_doctor.doctor_command, ["--json"])

    assert result.exit_code == 0, result.output
    assert calls == [False]
    assert json.loads(result.output) == report.to_dict()
    assert result.output == json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n"


def test_doctor_text_exits_one_when_a_required_check_fails(monkeypatch) -> None:
    from yt_insights import cli_doctor

    report = DoctorReport(
        data_root="/safe/corpus",
        checks=(
            CheckResult("yt-dlp", "fail", "not available"),
            CheckResult("search-index", "warn", "not built"),
        ),
    )
    monkeypatch.setattr(cli_doctor, "inspect_runtime", lambda config, **kwargs: report)

    result = CliRunner().invoke(cli_doctor.doctor_command, [])

    assert result.exit_code == 1
    assert result.output == (
        "Data root: /safe/corpus\n"
        "FAIL    yt-dlp: not available\n"
        "WARN    search-index: not built\n"
    )


def test_doctor_passes_probe_flag_to_the_read_only_inspector(monkeypatch) -> None:
    from yt_insights import cli_doctor

    calls: list[bool] = []

    def fake_inspect(config, *, probe_backends: bool = False) -> DoctorReport:
        calls.append(probe_backends)
        return DoctorReport(data_root="/safe/corpus", checks=())

    monkeypatch.setattr(cli_doctor, "inspect_runtime", fake_inspect)

    result = CliRunner().invoke(cli_doctor.doctor_command, ["--probe-backends"])

    assert result.exit_code == 0, result.output
    assert calls == [True]


def test_doctor_invalid_option_uses_click_exit_two() -> None:
    from yt_insights.cli_doctor import doctor_command

    result = CliRunner().invoke(doctor_command, ["--not-a-doctor-option"])

    assert result.exit_code == 2
    assert "No such option" in result.output
