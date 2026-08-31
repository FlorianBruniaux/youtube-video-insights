from __future__ import annotations

import http.client
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

from yt_insights.web.application import WebApplication
from yt_insights.web.models import WebRequest, WebResponse
from yt_insights.web.server import create_server

_SESSION_ID = "session1234567890abcdef1234567890ab"


class RecordingApplication:
    def __init__(self) -> None:
        self.requests: list[WebRequest] = []

    def handle(self, request: WebRequest) -> WebResponse:
        self.requests.append(request)
        response = WebResponse.json(
            200,
            {
                "body_size": len(request.body),
                "method": request.method,
                "path": request.path,
                "query": {key: list(values) for key, values in request.query.items()},
            },
        )
        return WebResponse(
            response.status,
            response.body,
            response.content_type,
            (("Access-Control-Allow-Origin", "*"),),
        )


@pytest.fixture
def static_root(tmp_path: Path) -> Path:
    root = tmp_path / "static"
    files = {
        "index.html": b"dashboard-shell",
        "search/index.html": b"search-shell",
        "sources/index.html": b"sources-shell",
        "research/new/index.html": b"new-research-shell",
        "research/workspace/index.html": b"research-workspace-shell",
        "exports/index.html": b"exports-shell",
        "_astro/app.A1b2.js": b"console.log('packaged')",
        "_astro/app.A1b2.css": b"body{color:#111}",
        "_astro/notes.txt": b"must-not-be-served",
        "_astro/nested/hidden.js": b"must-not-be-served",
    }
    for relative_path, body in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    secret = tmp_path / "pyproject.toml"
    secret.write_bytes(b"outside-static-secret")
    (root / "_astro" / "leak.js").symlink_to(secret)
    return root


@contextmanager
def running_server(
    application: RecordingApplication,
    static_root: Path,
    *,
    host: str = "127.0.0.1",
) -> Iterator[tuple[ThreadingHTTPServer, threading.Thread]]:
    server = create_server(
        cast(WebApplication, application),
        host=host,
        port=0,
        static_root=static_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, thread
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def request(
    server: ThreadingHTTPServer,
    method: str,
    target: str,
    *,
    body: bytes | None = None,
    host_header: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    address = server.server_address
    connect_host = str(address[0])
    port = int(address[1])
    expected_host = (
        f"[{connect_host}]:{port}" if ":" in connect_host else f"{connect_host}:{port}"
    )
    request_headers = {"Host": host_header or expected_host, **(headers or {})}
    connection = http.client.HTTPConnection(connect_host, port, timeout=2.0)
    try:
        connection.request(method, target, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            response_body,
        )
    finally:
        connection.close()


def bootstrap_token(server: ThreadingHTTPServer) -> str:
    status, headers, body = request(server, "GET", "/api/v1/bootstrap")
    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    payload = json.loads(body)
    token = payload["mutation_token"]
    assert isinstance(token, str) and len(token) >= 32
    return token


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "127.0.0.2"])
def test_server_rejects_every_non_exact_loopback_bind(
    static_root: Path, host: str
) -> None:
    """Allowing aliases or wildcard binds would expose an unauthenticated local service."""
    application = RecordingApplication()

    with pytest.raises(ValueError, match="loopback"):
        create_server(
            cast(WebApplication, application),
            host=host,
            port=0,
            static_root=static_root,
        )


def test_valid_loopback_get_builds_the_framework_neutral_request(
    static_root: Path,
) -> None:
    """Dropping repeated query values would change the application API contract."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, headers, body = request(server, "GET", "/api/v1/status?tag=one&tag=two")

    assert status == 200
    assert json.loads(body) == {
        "body_size": 0,
        "method": "GET",
        "path": "/api/v1/status",
        "query": {"tag": ["one", "two"]},
    }
    assert headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in headers
    assert application.requests[0].path == "/api/v1/status"


def test_exact_active_host_is_required_and_duplicate_host_is_rejected(
    static_root: Path,
) -> None:
    """Accepting an attacker-controlled Host would permit DNS rebinding."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, _, _ = request(
            server, "GET", "/api/v1/status", host_header="attacker.invalid"
        )
        address = server.server_address
        connection = http.client.HTTPConnection(
            str(address[0]), address[1], timeout=2.0
        )
        try:
            connection.putrequest("GET", "/api/v1/status", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{address[1]}")
            connection.putheader("Host", "attacker.invalid")
            connection.endheaders()
            duplicate_response = connection.getresponse()
            duplicate_status = duplicate_response.status
            duplicate_response.read()
        finally:
            connection.close()

    assert status == 403
    assert duplicate_status == 403
    assert application.requests == []


def test_ipv6_requires_the_exact_bracketed_active_host(static_root: Path) -> None:
    """Unbracketed or portless IPv6 Host values would weaken active-listener matching."""
    if not socket.has_ipv6:
        pytest.skip("IPv6 is unavailable")
    application = RecordingApplication()
    try:
        manager = running_server(application, static_root, host="::1")
        with manager as (server, _):
            address = server.server_address
            port = int(address[1])
            valid_status, _, _ = request(
                server,
                "GET",
                "/api/v1/status",
                host_header=f"[::1]:{port}",
            )
            invalid_status, _, _ = request(
                server,
                "GET",
                "/api/v1/status",
                host_header=f"::1:{port}",
            )
    except OSError as exc:
        pytest.skip(f"IPv6 loopback is unavailable: {exc}")

    assert valid_status == 200
    assert invalid_status == 403
    assert len(application.requests) == 1


def test_bootstrap_is_non_executable_uncached_json_without_a_cookie(
    static_root: Path,
) -> None:
    """An executable cross-origin bootstrap could disclose the mutation token."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, headers, body = request(server, "GET", "/api/v1/bootstrap")
        token = json.loads(body)["mutation_token"]
        script_status, script_headers, script_body = request(
            server, "GET", "/bootstrap.js"
        )

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers
    assert "set-cookie" not in headers
    assert script_status == 404
    assert "javascript" not in script_headers["content-type"]
    assert token.encode() not in script_body


def test_every_post_requires_the_process_token_before_dispatch(
    static_root: Path,
) -> None:
    """Dispatching an unauthenticated POST would allow cross-site local mutations."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        token = bootstrap_token(server)
        missing_status, _, _ = request(
            server, "POST", "/api/v1/research/sessions", body=b"{}"
        )
        wrong_status, _, _ = request(
            server,
            "POST",
            "/api/v1/research/sessions",
            body=b"{}",
            headers={"X-YT-Insights-Token": token + "wrong"},
        )
        accepted_status, _, _ = request(
            server,
            "POST",
            "/api/v1/research/sessions",
            body=b"{}",
            headers={"X-YT-Insights-Token": token},
        )

    assert (missing_status, wrong_status, accepted_status) == (403, 403, 200)
    assert [item.method for item in application.requests] == ["POST"]


def test_request_target_boundary_is_checked_before_url_parsing(
    static_root: Path,
) -> None:
    """Parsing a target beyond the fixed byte budget would admit unbounded input."""
    application = RecordingApplication()
    prefix = "/api/v1/status?q="
    accepted_target = prefix + "a" * (2_048 - len(prefix))
    rejected_target = accepted_target + "b"
    assert len(accepted_target.encode("ascii")) == 2_048
    assert len(rejected_target.encode("ascii")) == 2_049

    with running_server(application, static_root) as (server, _):
        accepted_status, _, _ = request(server, "GET", accepted_target)
        rejected_status, _, rejected_body = request(server, "GET", rejected_target)

    assert accepted_status == 200
    assert rejected_status == 414
    assert rejected_target.encode() not in rejected_body
    assert len(application.requests) == 1


def test_request_body_boundary_is_enforced_before_dispatch(static_root: Path) -> None:
    """Reading a 65,537-byte mutation body would cross the documented memory bound."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        token = bootstrap_token(server)
        headers = {"X-YT-Insights-Token": token}
        accepted_status, _, accepted_body = request(
            server,
            "POST",
            "/api/v1/research/sessions",
            body=b"a" * 65_536,
            headers=headers,
        )
        rejected_status, _, rejected_body = request(
            server,
            "POST",
            "/api/v1/research/sessions",
            body=b"a" * 65_537,
            headers=headers,
        )

    assert accepted_status == 200
    assert json.loads(accepted_body)["body_size"] == 65_536
    assert rejected_status == 413
    assert b"a" * 100 not in rejected_body
    assert len(application.requests) == 1


def test_content_length_is_bounded_before_integer_conversion(static_root: Path) -> None:
    """Converting an attacker-sized decimal header could consume unbounded CPU or fail."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        token = bootstrap_token(server)
        address = server.server_address
        port = int(address[1])
        connection = http.client.HTTPConnection(str(address[0]), port, timeout=2.0)
        try:
            connection.putrequest("POST", "/api/v1/research/sessions", skip_host=True)
            connection.putheader("Host", f"127.0.0.1:{port}")
            connection.putheader("X-YT-Insights-Token", token)
            connection.putheader("Content-Length", "9" * 5_000)
            connection.endheaders()
            response = connection.getresponse()
            status = response.status
            response.read()
        finally:
            connection.close()

    assert status == 400
    assert application.requests == []


@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/status%",
        "/api/v1/status%G0",
        "/api/v1/status%0G",
        "/api/v1/status?q=%FF",
    ],
)
def test_malformed_percent_encodings_are_rejected_without_dispatch(
    static_root: Path, target: str
) -> None:
    """Replacement-decoding malformed targets could change security-sensitive routes."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, _, _ = request(server, "GET", target)

    assert status == 400
    assert application.requests == []


@pytest.mark.parametrize(
    ("route", "expected"),
    [
        ("/", b"dashboard-shell"),
        ("/index.html", b"dashboard-shell"),
        ("/search", b"search-shell"),
        ("/search/", b"search-shell"),
        ("/sources", b"sources-shell"),
        ("/research/new", b"new-research-shell"),
        ("/exports", b"exports-shell"),
        ("/_astro/app.A1b2.js", b"console.log('packaged')"),
        ("/_astro/app.A1b2.css", b"body{color:#111}"),
    ],
)
def test_only_fixed_page_and_direct_asset_routes_are_served(
    static_root: Path, route: str, expected: bytes
) -> None:
    """A missing exact route entry must never fall back to a filesystem join."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, headers, body = request(server, "GET", route)

    assert status == 200
    assert body == expected
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize(
    "route",
    [
        "/../pyproject.toml",
        "/%2e%2e/pyproject.toml",
        "/_astro/../pyproject.toml",
        "/_astro//app.A1b2.js",
        "/Users/example/pyproject.toml",
        "/_astro/notes.txt",
        "/_astro/leak.js",
        "/_astro/nested/hidden.js",
        "/search/unknown",
    ],
)
def test_static_confinement_rejects_traversal_symlinks_and_unknowns(
    static_root: Path, route: str
) -> None:
    """Serving a request-derived or symlinked resource could escape the package root."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, _, body = request(server, "GET", route)

    assert status in {400, 404}
    assert b"outside-static-secret" not in body
    assert b"must-not-be-served" not in body
    assert b"pyproject.toml" not in body


def test_research_session_route_serves_only_the_fixed_workspace_shell(
    static_root: Path,
) -> None:
    """Using the session ID as a resource path would turn the rewrite into traversal."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        valid_status, _, valid_body = request(server, "GET", f"/research/{_SESSION_ID}")
        invalid_status, _, invalid_body = request(server, "GET", "/research/not.valid")
        encoded_slash_status, _, _ = request(server, "GET", "/research/safe%2Funsafe")

    assert valid_status == 200
    assert valid_body == b"research-workspace-shell"
    assert invalid_status == 404
    assert invalid_body != b"research-workspace-shell"
    assert encoded_slash_status == 404


def test_research_session_route_is_absent_without_the_packaged_shell(
    tmp_path: Path,
) -> None:
    """Inventing a shell when the package resource is missing would hide bad builds."""
    static_root = tmp_path / "empty-static"
    static_root.mkdir()
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        status, _, _ = request(server, "GET", f"/research/{_SESSION_ID}")

    assert status == 404


def test_shutdown_stops_the_serving_thread(static_root: Path) -> None:
    """A shutdown that leaves serve_forever alive would hang CLI termination."""
    application = RecordingApplication()
    server = create_server(
        cast(WebApplication, application),
        host="127.0.0.1",
        port=0,
        static_root=static_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        status, _, _ = request(server, "GET", "/api/v1/status")
        assert status == 200
        server.shutdown()
        thread.join(timeout=2.0)
        assert not thread.is_alive()
    finally:
        server.server_close()
