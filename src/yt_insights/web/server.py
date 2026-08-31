"""Bounded loopback HTTP transport for the local web application."""

from __future__ import annotations

import json
import re
import secrets
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

from .api import RequestValidationError, validate_session_id
from .application import WebApplication
from .models import WebRequest
from .security import (
    MUTATION_TOKEN_HEADER,
    expected_host_header,
    mutation_token_matches,
    security_headers,
    validate_bind_host,
)

_MAX_TARGET_BYTES = 2_048
_MAX_BODY_BYTES = 65_536
_BAD_PERCENT_ENCODING = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTENT_LENGTH = re.compile(r"[0-9]{1,5}")
_RESEARCH_ROUTE = re.compile(r"/research/([^/]+)")
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")

_PAGE_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("/", "/index.html"), ("index.html",)),
    (("/search", "/search/", "/search/index.html"), ("search", "index.html")),
    (("/sources", "/sources/", "/sources/index.html"), ("sources", "index.html")),
    (
        ("/research/new", "/research/new/", "/research/new/index.html"),
        ("research", "new", "index.html"),
    ),
    (("/exports", "/exports/", "/exports/index.html"), ("exports", "index.html")),
)
_WORKSPACE_RESOURCE = ("research", "workspace", "index.html")
_ASSET_CONTENT_TYPES = MappingProxyType(
    {
        ".avif": "image/avif",
        ".css": "text/css; charset=utf-8",
        ".ico": "image/x-icon",
        ".js": "text/javascript; charset=utf-8",
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".woff2": "font/woff2",
    }
)
_MANAGED_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "connection",
        "content-length",
        "content-security-policy",
        "content-type",
        "date",
        "permissions-policy",
        "referrer-policy",
        "server",
        "set-cookie",
        "transfer-encoding",
        "x-content-type-options",
    }
)


@dataclass(frozen=True, slots=True)
class _StaticResponse:
    body: bytes
    content_type: str


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        app: WebApplication,
        static_root: Traversable,
    ) -> None:
        self.web_application = app
        routes, workspace_shell = _build_static_routes(static_root)
        self.static_routes = routes
        self.workspace_shell = workspace_shell
        self.mutation_token = secrets.token_urlsafe(32)
        super().__init__(address, _RequestHandler)
        self.expected_host = expected_host_header(
            address[0], int(self.server_address[1])
        )


class _IPv6LoopbackServer(_LoopbackServer):
    address_family = socket.AF_INET6


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "YTInsights"
    sys_version = ""

    def do_GET(self) -> None:
        server = cast(_LoopbackServer, self.server)
        if not self._valid_host(server):
            self._error(403, "forbidden")
            return
        parsed = self._parse_target()
        if isinstance(parsed, int):
            self._error(parsed, "invalid_request")
            return
        path, query = parsed
        if path == "/api/v1/bootstrap":
            self._bootstrap(server)
            return
        if path.startswith("/api/"):
            self._dispatch(server, "GET", path, query, b"")
            return
        static_response = server.static_routes.get(path)
        if static_response is not None:
            self._respond(
                200,
                static_response.body,
                static_response.content_type,
                api=False,
            )
            return
        match = _RESEARCH_ROUTE.fullmatch(path)
        if match is not None and server.workspace_shell is not None:
            try:
                validate_session_id(match.group(1))
            except RequestValidationError:
                pass
            else:
                self._respond(
                    200,
                    server.workspace_shell.body,
                    server.workspace_shell.content_type,
                    api=False,
                )
                return
        self._error(404, "not_found")

    def do_POST(self) -> None:
        server = cast(_LoopbackServer, self.server)
        if not self._valid_host(server):
            self._error(403, "forbidden")
            return
        token_values = self.headers.get_all(MUTATION_TOKEN_HEADER, failobj=[])
        if not mutation_token_matches(token_values, server.mutation_token):
            self._error(403, "forbidden")
            return
        parsed = self._parse_target()
        if isinstance(parsed, int):
            self._error(parsed, "invalid_request")
            return
        body = self._read_body()
        if isinstance(body, int):
            self._error(body, "invalid_request")
            return
        path, query = parsed
        self._dispatch(server, "POST", path, query, body)

    def log_message(self, format: str, *args: object) -> None:
        """Keep rejected local request targets out of process output."""

    def _valid_host(self, server: _LoopbackServer) -> bool:
        values = self.headers.get_all("Host", failobj=[])
        return len(values) == 1 and values[0] == server.expected_host

    def _parse_target(self) -> tuple[str, Mapping[str, tuple[str, ...]]] | int:
        try:
            raw_target = self.path.encode("ascii")
        except UnicodeEncodeError:
            return 400
        if len(raw_target) > _MAX_TARGET_BYTES:
            return 414
        if _BAD_PERCENT_ENCODING.search(self.path) is not None:
            return 400
        try:
            parts = urlsplit(self.path)
            if (
                parts.scheme
                or parts.netloc
                or parts.fragment
                or not parts.path.startswith("/")
            ):
                return 400
            path = unquote_to_bytes(parts.path).decode("utf-8", errors="strict")
            parsed_query = parse_qs(
                parts.query,
                keep_blank_values=True,
                strict_parsing=False,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeDecodeError, ValueError):
            return 400
        return path, MappingProxyType(
            {key: tuple(values) for key, values in parsed_query.items()}
        )

    def _read_body(self) -> bytes | int:
        if self.headers.get_all("Transfer-Encoding", failobj=[]):
            return 400
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if not lengths:
            return b""
        if len(lengths) != 1 or _CONTENT_LENGTH.fullmatch(lengths[0]) is None:
            return 400
        length = int(lengths[0])
        if length > _MAX_BODY_BYTES:
            return 413
        body = self.rfile.read(length)
        if len(body) != length:
            return 400
        return body

    def _bootstrap(self, server: _LoopbackServer) -> None:
        body = json.dumps(
            {"mutation_token": server.mutation_token, "schema_version": 1},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._respond(
            200,
            body,
            "application/json; charset=utf-8",
            api=True,
        )

    def _dispatch(
        self,
        server: _LoopbackServer,
        method: str,
        path: str,
        query: Mapping[str, tuple[str, ...]],
        body: bytes,
    ) -> None:
        request = WebRequest(method, path, query, self._request_headers(), body)
        try:
            response = server.web_application.handle(request)
        except Exception:
            self._error(500, "internal_error")
            return
        self._respond(
            response.status,
            response.body,
            response.content_type,
            api=True,
            extra_headers=response.headers,
        )

    def _request_headers(self) -> Mapping[str, str]:
        headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if name.lower() == MUTATION_TOKEN_HEADER.lower():
                continue
            headers[name.lower()] = value
        return MappingProxyType(headers)

    def _error(self, status: int, code: str) -> None:
        body = json.dumps(
            {"error": {"code": code}, "schema_version": 1},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._respond(
            status,
            body,
            "application/json; charset=utf-8",
            api=True,
        )

    def _respond(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        api: bool,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in security_headers(api=api):
            self.send_header(name, value)
        for name, value in extra_headers:
            normalized_name = name.lower()
            if (
                normalized_name in _MANAGED_RESPONSE_HEADERS
                or normalized_name.startswith("access-control-")
                or _HEADER_NAME.fullmatch(name) is None
                or "\r" in value
                or "\n" in value
            ):
                continue
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def create_server(
    app: WebApplication,
    *,
    host: str,
    port: int,
    static_root: Traversable,
) -> ThreadingHTTPServer:
    """Create an inactive exact-loopback server with a frozen static table."""
    validated_host = validate_bind_host(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    server_class: type[_LoopbackServer] = (
        _IPv6LoopbackServer if validated_host == "::1" else _LoopbackServer
    )
    return server_class((validated_host, port), app, static_root)


def _build_static_routes(
    static_root: Traversable,
) -> tuple[Mapping[str, _StaticResponse], _StaticResponse | None]:
    routes: dict[str, _StaticResponse] = {}
    if not _safe_directory(static_root):
        return MappingProxyType(routes), None
    for public_routes, parts in _PAGE_ROUTES:
        response = _read_static_response(static_root, parts, "text/html; charset=utf-8")
        if response is None:
            continue
        for public_route in public_routes:
            routes[public_route] = response
    workspace = _read_static_response(
        static_root, _WORKSPACE_RESOURCE, "text/html; charset=utf-8"
    )
    asset_root = _fixed_descendant(static_root, ("_astro",))
    if asset_root is not None and _safe_directory(asset_root):
        for child in asset_root.iterdir():
            name = child.name
            suffix = Path(name).suffix.lower()
            content_type = _ASSET_CONTENT_TYPES.get(suffix)
            if (
                _ASSET_NAME.fullmatch(name) is None
                or content_type is None
                or not _regular_file(child)
            ):
                continue
            routes[f"/_astro/{name}"] = _StaticResponse(
                child.read_bytes(), content_type
            )
    return MappingProxyType(routes), workspace


def _read_static_response(
    root: Traversable,
    parts: tuple[str, ...],
    content_type: str,
) -> _StaticResponse | None:
    resource = _fixed_descendant(root, parts)
    if resource is None or not _regular_file(resource):
        return None
    return _StaticResponse(resource.read_bytes(), content_type)


def _fixed_descendant(root: Traversable, parts: tuple[str, ...]) -> Traversable | None:
    resource = root
    try:
        for part in parts:
            resource = resource.joinpath(part)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    return resource


def _safe_directory(resource: Traversable) -> bool:
    if isinstance(resource, Path) and resource.is_symlink():
        return False
    try:
        return resource.is_dir()
    except OSError:
        return False


def _regular_file(resource: Traversable) -> bool:
    if isinstance(resource, Path) and resource.is_symlink():
        return False
    try:
        return resource.is_file()
    except OSError:
        return False
