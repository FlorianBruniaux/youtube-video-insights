"""Read-only, secret-safe runtime diagnostics for agent integrations."""

from __future__ import annotations

import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

from .config import Config
from .search.sqlite_fts import SearchIndexError, SQLiteFtsIndex

CheckStatus = Literal["pass", "warn", "fail", "unknown"]

_CC_BRIDGE_HEALTH_URL = "http://127.0.0.1:4141/health"
_OLLAMA_HEALTH_URL = "http://127.0.0.1:11434/api/tags"
_LOCAL_PROBE_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One stable diagnostic result without operational secrets."""

    name: str
    status: CheckStatus
    detail: str
    configured: bool | None = None

    def __post_init__(self) -> None:
        if not self.name or not self.detail:
            raise ValueError("doctor check name and detail must be non-empty")
        if self.status not in {"pass", "warn", "fail", "unknown"}:
            raise ValueError("unsupported doctor check status")

    def to_dict(self) -> dict[str, str | bool]:
        payload: dict[str, str | bool] = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.configured is not None:
            payload["configured"] = self.configured
        return payload


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Complete deterministic runtime report."""

    data_root: str
    checks: tuple[CheckResult, ...]

    @property
    def has_failures(self) -> bool:
        return any(check.status == "fail" for check in self.checks)

    def to_dict(self) -> dict[str, str | list[dict[str, str | bool]]]:
        return {
            "data_root": self.data_root,
            "checks": [check.to_dict() for check in self.checks],
        }


def _executable_check(name: str, *, required: bool) -> CheckResult:
    if shutil.which(name) is not None:
        return CheckResult(name, "pass", "available")
    return CheckResult(name, "fail" if required else "warn", "not available")


def _search_index_check(database: Path) -> CheckResult:
    try:
        database.lstat()
    except FileNotFoundError:
        return CheckResult("search-index", "warn", "not built")
    except OSError:
        return CheckResult("search-index", "fail", "cannot be inspected")

    try:
        report = SQLiteFtsIndex(database).status()
    except SearchIndexError:
        return CheckResult("search-index", "fail", "invalid")
    except OSError:
        return CheckResult("search-index", "fail", "cannot be inspected")
    return CheckResult(
        "search-index",
        "pass",
        f"{report.documents_indexed} documents, {report.passages_indexed} passages",
    )


def _catalog_check(database: Path) -> CheckResult:
    """Inspect catalog presence without opening SQLite or initializing a schema."""
    try:
        details = database.lstat()
    except FileNotFoundError:
        return CheckResult("catalog", "warn", "not built")
    except OSError:
        return CheckResult("catalog", "warn", "cannot be inspected")
    if not stat.S_ISREG(details.st_mode):
        return CheckResult("catalog", "warn", "path is not a regular file")
    return CheckResult("catalog", "pass", "present")


def _cloud_credential_check(config: Config) -> CheckResult:
    configured = bool(
        config.api_key
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("YT_INSIGHTS_API_KEY")
    )
    return CheckResult(
        "cloud-credential",
        "pass" if configured else "unknown",
        "configured" if configured else "not configured",
        configured=configured,
    )


def _probe_local_backend(name: str, url: str) -> CheckResult:
    try:
        with httpx.Client(
            timeout=_LOCAL_PROBE_TIMEOUT_SECONDS,
            trust_env=False,
        ) as client:
            response = client.get(url)
    except (httpx.RequestError, OSError):
        return CheckResult(name, "warn", "unreachable")
    if 200 <= response.status_code < 400:
        return CheckResult(name, "pass", "reachable")
    return CheckResult(name, "warn", f"HTTP {response.status_code}")


def inspect_runtime(config: Config, *, probe_backends: bool = False) -> DoctorReport:
    """Inspect local runtime state without writing files or calling an LLM."""
    paths = config.data_paths
    checks = [
        _executable_check("yt-dlp", required=True),
        _executable_check("ffmpeg", required=False),
        _search_index_check(paths.search_database),
        _catalog_check(paths.catalog_database),
        _cloud_credential_check(config),
    ]
    if probe_backends:
        checks.extend(
            (
                _probe_local_backend("cc-bridge", _CC_BRIDGE_HEALTH_URL),
                _probe_local_backend("ollama", _OLLAMA_HEALTH_URL),
            )
        )
    return DoctorReport(data_root=str(paths.root), checks=tuple(checks))


__all__ = ["CheckResult", "DoctorReport", "inspect_runtime"]
