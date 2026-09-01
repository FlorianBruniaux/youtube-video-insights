from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import ThreadingHTTPServer
from importlib.resources.abc import Traversable
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


class UnenumeratedFile:
    name = "index.html"

    def is_dir(self) -> bool:
        return False

    def is_file(self) -> bool:
        return True

    def iterdir(self) -> Iterator[UnenumeratedFile]:
        return iter(())

    def joinpath(self, *descendants: str) -> UnenumeratedFile:
        return self

    def open(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("open is not part of this witness")

    def read_bytes(self) -> bytes:
        return b"unconfined-generic-shell"

    def read_text(self, encoding: str | None = None, errors: str | None = None) -> str:
        return self.read_bytes().decode(encoding or "utf-8", errors or "strict")

    def __truediv__(self, child: str) -> UnenumeratedFile:
        return self.joinpath(child)


class UnenumeratedRoot:
    name = "static"

    def is_dir(self) -> bool:
        return True

    def is_file(self) -> bool:
        return False

    def iterdir(self) -> Iterator[UnenumeratedFile]:
        return iter(())

    def joinpath(self, *descendants: str) -> UnenumeratedFile:
        return UnenumeratedFile()

    def open(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("open is not part of this witness")

    def read_bytes(self) -> bytes:
        raise AssertionError("directories are not readable")

    def read_text(self, encoding: str | None = None, errors: str | None = None) -> str:
        raise AssertionError("directories are not readable")

    def __truediv__(self, child: str) -> UnenumeratedFile:
        return self.joinpath(child)


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
    static_root: Traversable,
    *,
    host: str = "127.0.0.1",
    max_request_workers: int = 16,
) -> Iterator[tuple[ThreadingHTTPServer, threading.Thread]]:
    server = create_server(
        cast(WebApplication, application),
        host=host,
        port=0,
        static_root=static_root,
        max_request_workers=max_request_workers,
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


def raw_exchange(
    server: ThreadingHTTPServer,
    payload: bytes,
    *,
    shutdown_write: bool = True,
    timeout: float = 2.0,
) -> bytes:
    address = server.server_address
    connection = socket.create_connection(
        (str(address[0]), int(address[1])), timeout=2.0
    )
    connection.settimeout(timeout)
    try:
        connection.sendall(payload)
        if shutdown_write:
            connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = connection.recv(65_536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        connection.close()


def raw_request_bytes(
    server: ThreadingHTTPServer,
    method: bytes,
    target: bytes,
    *,
    extra_headers: tuple[bytes, ...] = (),
) -> bytes:
    address = server.server_address
    host = str(address[0])
    port = int(address[1])
    host_value = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    headers = (f"Host: {host_value}".encode("ascii"), *extra_headers)
    return raw_exchange(
        server,
        b" ".join((method, target, b"HTTP/1.1\r\n"))
        + b"\r\n".join(headers)
        + b"\r\n\r\n",
    )


def parse_raw_response(response: bytes) -> tuple[int, dict[str, str], bytes]:
    head, body = response.split(b"\r\n\r\n", 1)
    lines = head.decode("iso-8859-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {
        name.lower(): value.strip()
        for name, value in (line.split(":", 1) for line in lines[1:])
    }
    return status, headers, body


def assert_fixed_json_error(response: bytes, expected_status: int) -> None:
    status, headers, body = parse_raw_response(response)
    assert status == expected_status
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["content-security-policy"]
    assert "access-control-allow-origin" not in headers
    payload = json.loads(body)
    assert payload["schema_version"] == 1
    assert set(payload) == {"error", "schema_version"}


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


def test_raw_target_limit_precedes_leading_slash_normalization(
    static_root: Path,
) -> None:
    """Normalizing thousands of leading slashes first would bypass the byte cap."""
    application = RecordingApplication()
    target = b"/" * 2_049 + b"api/v1/status"

    with running_server(application, static_root) as (server, _):
        response = raw_request_bytes(server, b"GET", target)

    assert_fixed_json_error(response, 414)
    assert target not in response
    assert application.requests == []


def test_doubled_leading_slash_is_rejected_before_framework_normalization(
    static_root: Path,
) -> None:
    """Collapsing //api into /api would dispatch a request with different authority semantics."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        response = raw_request_bytes(server, b"GET", b"//api/v1/status")

    assert_fixed_json_error(response, 400)
    assert application.requests == []


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


def test_non_ascii_mutation_token_returns_the_fixed_403(static_root: Path) -> None:
    """A Latin-1 token must not escape the constant-time comparison as a TypeError."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        response = raw_request_bytes(
            server,
            b"POST",
            b"/api/v1/research/sessions",
            extra_headers=(
                b"X-YT-Insights-Token: \xff",
                b"Content-Length: 0",
            ),
        )

    assert_fixed_json_error(response, 403)
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


def test_symlinked_static_directories_are_never_mapped(tmp_path: Path) -> None:
    """Checking only a final file would follow a symlinked page-shell ancestor."""
    root = tmp_path / "static"
    root.mkdir()
    outside = tmp_path / "outside"
    (outside / "search").mkdir(parents=True)
    (outside / "search" / "index.html").write_bytes(b"escaped-search-shell")
    (outside / "_astro").mkdir()
    (outside / "_astro" / "escaped.js").write_bytes(b"escaped-asset")
    (root / "search").symlink_to(outside / "search", target_is_directory=True)
    (root / "_astro").symlink_to(outside / "_astro", target_is_directory=True)
    application = RecordingApplication()

    with running_server(application, root) as (server, _):
        page_status, _, page_body = request(server, "GET", "/search")
        asset_status, _, asset_body = request(server, "GET", "/_astro/escaped.js")

    assert (page_status, asset_status) == (404, 404)
    assert b"escaped" not in page_body + asset_body


def test_static_file_replacement_after_validation_reads_the_opened_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replacing a validated path before reading must not substitute new bytes."""
    root = tmp_path / "static"
    root.mkdir()
    target = root / "index.html"
    target.write_bytes(b"original-shell")
    target_inode = os.stat(target, follow_symlinks=False).st_ino
    replacement = b"replacement-shell"
    replaced = False
    original_is_file = Path.is_file
    original_fstat = os.fstat

    def replace_target() -> None:
        nonlocal replaced
        if replaced:
            return
        replaced = True
        target.rename(root / "original-index.html")
        target.write_bytes(replacement)

    def racing_is_file(path: Path) -> bool:
        result = original_is_file(path)
        if path == target and result:
            replace_target()
        return result

    def racing_fstat(descriptor: int) -> os.stat_result:
        result = original_fstat(descriptor)
        if result.st_ino == target_inode:
            replace_target()
        return result

    monkeypatch.setattr(Path, "is_file", racing_is_file)
    monkeypatch.setattr(os, "fstat", racing_fstat)
    application = RecordingApplication()

    with running_server(application, root) as (server, _):
        status, _, body = request(server, "GET", "/")

    assert replaced
    assert status == 200
    assert body == b"original-shell"


def test_unenumerated_generic_descendant_is_rejected(tmp_path: Path) -> None:
    """A generic joinpath result absent from its parent listing is not confined."""
    application = RecordingApplication()

    with running_server(
        application,
        cast(Traversable, UnenumeratedRoot()),
    ) as (server, _):
        status, _, body = request(server, "GET", "/")

    assert status == 404
    assert b"unconfined-generic-shell" not in body


def test_zip_package_traversable_loads_fixed_descendants_once(tmp_path: Path) -> None:
    """Rejecting every non-Path resource would break valid packaged zip resources."""
    archive_path = tmp_path / "assets.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("static/index.html", b"zip-dashboard-shell")
        archive.writestr("static/_astro/app.Zip1.js", b"zip-asset")
    application = RecordingApplication()

    with zipfile.ZipFile(archive_path) as archive:
        resource = zipfile.Path(archive, "static/")
        with running_server(
            application,
            resource,
        ) as (server, _):
            page_status, _, page_body = request(server, "GET", "/")
            asset_status, _, asset_body = request(server, "GET", "/_astro/app.Zip1.js")

    assert (page_status, asset_status) == (200, 200)
    assert page_body == b"zip-dashboard-shell"
    assert asset_body == b"zip-asset"


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


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (b"GET /private/canary extra HTTP/1.1\r\n\r\n", 400),
        (b"GET / HTTP/1.1\r\nX-Oversized: " + b"a" * 65_537 + b"\r\n\r\n", 431),
    ],
)
def test_parser_failures_use_fixed_secure_json(
    static_root: Path, payload: bytes, status: int
) -> None:
    """Inherited send_error would reflect parser input in an HTML response."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        response = raw_exchange(server, payload)

    assert_fixed_json_error(response, status)
    assert b"private/canary" not in response
    assert b"X-Oversized" not in response
    assert application.requests == []


@pytest.mark.parametrize(
    "method", [b"HEAD", b"OPTIONS", b"PUT", b"PATCH", b"DELETE", b"BREW"]
)
def test_every_unsupported_method_uses_fixed_secure_json(
    static_root: Path, method: bytes
) -> None:
    """An inherited unsupported method would bypass the transport security headers."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        response = raw_request_bytes(server, method, b"/private/method-canary")

    assert_fixed_json_error(response, 405)
    assert b"method-canary" not in response
    assert application.requests == []


def test_incomplete_headers_are_closed_within_the_connection_timeout(
    static_root: Path,
) -> None:
    """A client that never terminates headers must not retain a worker indefinitely."""
    application = RecordingApplication()

    with running_server(application, static_root) as (server, _):
        address = server.server_address
        client = socket.create_connection(
            (str(address[0]), int(address[1])), timeout=2.0
        )
        client.settimeout(2.0)
        started = time.monotonic()
        try:
            client.sendall(b"GET / HTTP/1.1\r\nHost: incomplete")
            assert client.recv(1) == b""
        finally:
            client.close()
        elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert application.requests == []


def test_request_worker_limit_rejects_excess_connections_with_fixed_json(
    static_root: Path,
) -> None:
    """Spawning one thread per accepted socket permits local resource exhaustion."""
    application = RecordingApplication()

    with running_server(
        application,
        static_root,
        max_request_workers=2,
    ) as (server, _):
        address = server.server_address
        port = int(address[1])
        host = str(address[0])
        clients = [socket.create_connection((host, port), timeout=2.0) for _ in range(2)]
        try:
            for client in clients:
                client.sendall(
                    b"GET /api/v1/status HTTP/1.1\r\n"
                    + f"Host: {host}:{port}\r\n".encode("ascii")
                )
            deadline = time.monotonic() + 0.5
            while len(tuple(vars(server).get("_worker_threads", ()))) < 2:
                if time.monotonic() >= deadline:
                    pytest.fail("request workers did not reach the configured limit")
                time.sleep(0.005)

            response = raw_request_bytes(server, b"GET", b"/api/v1/status")

            status, headers, body = parse_raw_response(response)
            assert status == 503
            assert headers["cache-control"] == "no-store"
            assert json.loads(body) == {
                "error": {"code": "server_busy"},
                "schema_version": 1,
            }
            assert len(tuple(vars(server).get("_worker_threads", ()))) <= 2
        finally:
            for client in clients:
                client.close()


def test_shutdown_closes_partial_post_and_prevents_late_dispatch(
    static_root: Path,
) -> None:
    """Completing a body after shutdown starts must never dispatch a mutation."""
    application = RecordingApplication()
    server = create_server(
        cast(WebApplication, application),
        host="127.0.0.1",
        port=0,
        static_root=static_root,
    )
    serving_thread = threading.Thread(target=server.serve_forever, daemon=True)
    serving_thread.start()
    token = bootstrap_token(server)
    address = server.server_address
    client = socket.create_connection((str(address[0]), int(address[1])), timeout=2.0)
    client.settimeout(1.0)
    try:
        port = int(address[1])
        client.sendall(
            b"POST /api/v1/research/sessions HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{port}\r\n".encode("ascii")
            + f"X-YT-Insights-Token: {token}\r\n".encode("ascii")
            + b"Content-Length: 10\r\n\r\n{"
        )
        time.sleep(0.05)
        server.shutdown()
        serving_thread.join(timeout=1.0)
        with suppress(OSError):
            client.sendall(b"123456789")
        time.sleep(0.05)

        workers = tuple(vars(server).get("_worker_threads", ()))
        active_sockets = tuple(vars(server).get("_active_sockets", ()))
        assert not serving_thread.is_alive()
        assert not any(worker.is_alive() for worker in workers)
        assert active_sockets == ()
        assert application.requests == []
    finally:
        client.close()
        server.server_close()


def test_invalid_application_content_type_becomes_a_fixed_error(
    static_root: Path,
) -> None:
    """A CRLF-bearing content type must not create a response header."""

    class InvalidContentTypeApplication(RecordingApplication):
        def handle(self, request: WebRequest) -> WebResponse:
            self.requests.append(request)
            return WebResponse(
                200,
                b"unsafe-response",
                "application/json\r\nX-Injected: yes",
            )

    application = InvalidContentTypeApplication()

    with running_server(application, static_root) as (server, _):
        status, headers, body = request(server, "GET", "/api/v1/status")

    assert status == 500
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert "x-injected" not in headers
    assert b"unsafe-response" not in body
