from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from yt_insights import cli_web
from yt_insights.cli import cli
from yt_insights.paths import DataPaths


class _FakeJobs:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def close(self) -> None:
        self._events.append("jobs-close")


class _FakeServer:
    server_address = ("127.0.0.1", 43210)

    def __init__(self, events: list[str], *, interrupt: bool = False) -> None:
        self._events = events
        self._interrupt = interrupt

    def serve_forever(self) -> None:
        self._events.append("serve")
        if self._interrupt:
            raise KeyboardInterrupt

    def server_close(self) -> None:
        self._events.append("server-close")


@pytest.mark.parametrize("port", ["0", "65536", "true", "false"])
def test_serve_rejects_ports_outside_the_cli_integer_contract(port: str) -> None:
    result = CliRunner().invoke(cli, ["serve", "--port", port, "--no-open"])

    assert result.exit_code == 2
    assert "Invalid value for '--port'" in result.output


def test_serve_binds_fixed_loopback_and_opens_browser_after_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    paths = DataPaths.from_root(Path("output"))

    def create_runtime(
        received_paths: DataPaths, *, host: str, port: int
    ) -> SimpleNamespace:
        assert received_paths == paths
        assert host == "127.0.0.1"
        assert port == 8765
        events.append("bound")
        return SimpleNamespace(
            server=_FakeServer(events),
            jobs=_FakeJobs(events),
        )

    monkeypatch.setattr(
        cli_web,
        "load_config",
        lambda _overrides: SimpleNamespace(data_paths=paths),
    )
    monkeypatch.setattr(cli_web, "create_web_runtime", create_runtime)
    monkeypatch.setattr(
        cli_web.webbrowser,
        "open",
        lambda url: events.append(f"open:{url}"),
    )

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 0, result.output
    assert result.output == "Serving YT Insights at http://127.0.0.1:43210/\n"
    assert events == [
        "bound",
        "open:http://127.0.0.1:43210/",
        "serve",
        "server-close",
        "jobs-close",
    ]


def test_serve_no_open_suppresses_browser_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    paths = DataPaths.from_root(Path("output"))
    monkeypatch.setattr(
        cli_web,
        "load_config",
        lambda _overrides: SimpleNamespace(data_paths=paths),
    )
    monkeypatch.setattr(
        cli_web,
        "create_web_runtime",
        lambda _paths, *, host, port: SimpleNamespace(
            server=_FakeServer(events),
            jobs=_FakeJobs(events),
        ),
    )
    monkeypatch.setattr(
        cli_web.webbrowser,
        "open",
        lambda url: events.append(f"open:{url}"),
    )

    result = CliRunner().invoke(cli, ["serve", "--no-open"])

    assert result.exit_code == 0, result.output
    assert not any(event.startswith("open:") for event in events)


def test_ctrl_c_closes_server_and_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    paths = DataPaths.from_root(Path("output"))
    monkeypatch.setattr(
        cli_web,
        "load_config",
        lambda _overrides: SimpleNamespace(data_paths=paths),
    )
    monkeypatch.setattr(
        cli_web,
        "create_web_runtime",
        lambda _paths, *, host, port: SimpleNamespace(
            server=_FakeServer(events, interrupt=True),
            jobs=_FakeJobs(events),
        ),
    )

    result = CliRunner().invoke(cli, ["serve", "--no-open"])

    assert result.exit_code == 0, result.output
    assert events == ["serve", "server-close", "jobs-close"]


def test_failed_bind_does_not_open_browser_or_disclose_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_path = "/private/example/corpus.sqlite3"
    paths = DataPaths.from_root(Path("output"))
    opened: list[str] = []
    monkeypatch.setattr(
        cli_web,
        "load_config",
        lambda _overrides: SimpleNamespace(data_paths=paths),
    )
    monkeypatch.setattr(
        cli_web,
        "create_web_runtime",
        lambda _paths, *, host, port: (_ for _ in ()).throw(
            RuntimeError(f"cannot bind near {private_path}")
        ),
    )
    monkeypatch.setattr(cli_web.webbrowser, "open", opened.append)

    result = CliRunner().invoke(cli, ["serve"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: Local web server is unavailable. "
        "Check the configured databases and port, then retry.\n"
    )
    assert private_path not in result.output
    assert opened == []


def test_missing_databases_return_bounded_path_free_click_error(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_root = tmp_path / "private-corpus-location"
    monkeypatch.setenv("YT_INSIGHTS_DATA_ROOT", str(private_root))

    result = CliRunner().invoke(cli, ["serve", "--no-open"])

    assert result.exit_code == 1
    assert result.output == (
        "Error: Local web server is unavailable. "
        "Check the configured databases and port, then retry.\n"
    )
    assert str(private_root) not in result.output
    assert len(result.output.encode("utf-8")) < 256
