from __future__ import annotations

import json
from pathlib import Path

import httpx

from yt_insights.config import Config
from yt_insights.search.models import BuildReport
from yt_insights.search.sqlite_fts import SearchIndexInvalid


def _checks_by_name(report) -> dict[str, object]:
    return {check.name: check for check in report.checks}


def test_doctor_json_contains_no_secret_values(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import doctor

    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-appear")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: f"/secret/bin/{name}")

    report = doctor.inspect_runtime(Config(data_root=tmp_path, api_key="also-secret"))
    payload = json.dumps(report.to_dict())

    assert "must-not-appear" not in payload
    assert "also-secret" not in payload
    assert "/secret/bin" not in payload
    assert report.data_root == str(tmp_path.resolve())
    cloud = _checks_by_name(report)["cloud-credential"]
    assert cloud.status == "pass"
    assert cloud.detail == "configured"
    assert cloud.configured is True
    assert report.to_dict()["checks"][4]["configured"] is True


def test_missing_tools_index_catalog_and_credentials_have_stable_severity(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import doctor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("YT_INSIGHTS_API_KEY", raising=False)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)

    report = doctor.inspect_runtime(Config(data_root=tmp_path))
    checks = _checks_by_name(report)

    assert (checks["yt-dlp"].status, checks["yt-dlp"].detail) == (
        "fail",
        "not available",
    )
    assert (checks["ffmpeg"].status, checks["ffmpeg"].detail) == (
        "warn",
        "not available",
    )
    assert checks["search-index"].status == "warn"
    assert checks["catalog"].status == "warn"
    assert (checks["cloud-credential"].status, checks["cloud-credential"].detail) == (
        "unknown",
        "not configured",
    )
    assert checks["cloud-credential"].configured is False
    assert report.has_failures is True


def test_existing_valid_index_reports_document_and_passage_counts(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import doctor

    config = Config(data_root=tmp_path)
    config.data_paths.search_database.parent.mkdir(parents=True)
    config.data_paths.search_database.touch()

    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tool")
    monkeypatch.setattr(
        doctor.SQLiteFtsIndex,
        "status",
        lambda self: BuildReport(12, 12, 0, 12, 87),
    )

    report = doctor.inspect_runtime(config)
    check = _checks_by_name(report)["search-index"]

    assert check.status == "pass"
    assert check.detail == "12 documents, 87 passages"
    assert report.has_failures is False


def test_existing_invalid_index_is_a_required_failure(tmp_path: Path, monkeypatch) -> None:
    from yt_insights import doctor

    config = Config(data_root=tmp_path)
    config.data_paths.search_database.parent.mkdir(parents=True)
    config.data_paths.search_database.write_bytes(b"not sqlite")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tool")

    def fail_status(self) -> BuildReport:
        raise SearchIndexInvalid("sensitive implementation detail")

    monkeypatch.setattr(doctor.SQLiteFtsIndex, "status", fail_status)

    report = doctor.inspect_runtime(config)
    check = _checks_by_name(report)["search-index"]

    assert (check.status, check.detail) == ("fail", "invalid")
    assert "sensitive implementation detail" not in json.dumps(report.to_dict())
    assert report.has_failures is True


def test_backend_probe_uses_only_local_get_requests_and_reports_unreachable(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import doctor

    requested: list[str] = []

    class FailingClient:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            assert 0 < timeout <= 2
            assert trust_env is False

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str):
            requested.append(url)
            raise httpx.ConnectError("unreachable")

        def post(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("doctor must never submit a completion")

    monkeypatch.setattr(doctor.httpx, "Client", FailingClient)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tool")

    report = doctor.inspect_runtime(Config(data_root=tmp_path), probe_backends=True)
    checks = _checks_by_name(report)

    assert requested == [
        "http://127.0.0.1:4141/health",
        "http://127.0.0.1:11434/api/tags",
    ]
    assert checks["cc-bridge"].status == "warn"
    assert checks["ollama"].status == "warn"
    assert all(url.startswith("http://127.0.0.1:") for url in requested)


def test_backend_probe_accepts_only_successful_local_health_responses(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import doctor

    class Response:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    class Client:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            assert trust_env is False

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            return Response(204 if url.endswith("/health") else 503)

    monkeypatch.setattr(doctor.httpx, "Client", Client)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tool")

    report = doctor.inspect_runtime(Config(data_root=tmp_path), probe_backends=True)
    checks = _checks_by_name(report)

    assert (checks["cc-bridge"].status, checks["cc-bridge"].detail) == (
        "pass",
        "reachable",
    )
    assert (checks["ollama"].status, checks["ollama"].detail) == (
        "warn",
        "HTTP 503",
    )


def test_backend_probe_never_inherits_environment_proxy_configuration(
    tmp_path: Path, monkeypatch
) -> None:
    from yt_insights import doctor

    client_options: list[tuple[float, bool]] = []

    class Response:
        status_code = 200

    class Client:
        def __init__(self, *, timeout: float, trust_env: bool) -> None:
            client_options.append((timeout, trust_env))

        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> Response:
            return Response()

    monkeypatch.setattr(doctor.httpx, "Client", Client)
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/usr/bin/tool")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.example.test:8080")

    doctor.inspect_runtime(Config(data_root=tmp_path), probe_backends=True)

    assert client_options == [(1.0, False), (1.0, False)]
