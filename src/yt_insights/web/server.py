"""Bounded loopback HTTP transport for the local web application."""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import stat
import threading
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import cast
from urllib.parse import parse_qs, unquote_to_bytes, urlsplit

from .api import RequestValidationError, validate_session_id
from .application import WebApplication
from .models import WebRequest, WebResponse
from .security import (
    MUTATION_TOKEN_HEADER,
    expected_host_header,
    mutation_token_matches,
    security_headers,
    validate_bind_host,
)

_MAX_TARGET_BYTES = 2_048
_MAX_BODY_BYTES = 65_536
_CONNECTION_TIMEOUT_SECONDS = 1.0
_DEFAULT_MAX_REQUEST_WORKERS = 16
_BAD_PERCENT_ENCODING = re.compile(r"%(?![0-9A-Fa-f]{2})")
_CONTENT_LENGTH = re.compile(r"[0-9]{1,5}")
_RESEARCH_ROUTE = re.compile(r"/research/([^/]+)")
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_CONTENT_TYPE = re.compile(r"[\x20-\x7e]+")
_READ_CHUNK_BYTES = 64 * 1024
_SocketRequest = socket.socket | tuple[bytes, socket.socket]

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
    daemon_threads = False

    def __init__(
        self,
        address: tuple[str, int],
        app: WebApplication,
        static_root: Traversable,
        max_request_workers: int,
    ) -> None:
        if (
            isinstance(max_request_workers, bool)
            or not isinstance(max_request_workers, int)
            or not 1 <= max_request_workers <= 128
        ):
            raise ValueError("request worker limit must be between 1 and 128")
        self._lifecycle_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._closing = False
        self._active_sockets: set[socket.socket] = set()
        self._worker_threads: set[threading.Thread] = set()
        self._request_slots = threading.BoundedSemaphore(max_request_workers)
        self.web_application = app
        routes, workspace_shell = _build_static_routes(static_root)
        self.static_routes = routes
        self.workspace_shell = workspace_shell
        self.mutation_token = secrets.token_urlsafe(32)
        super().__init__(address, _RequestHandler)
        self.expected_host = expected_host_header(
            address[0], int(self.server_address[1])
        )

    def get_request(self) -> tuple[socket.socket, object]:
        request, client_address = super().get_request()
        request.settimeout(_CONNECTION_TIMEOUT_SECONDS)
        with self._lifecycle_lock:
            if self._closing:
                request.close()
                raise OSError("server is closing")
            self._active_sockets.add(request)
        return request, client_address

    def process_request(self, request: _SocketRequest, client_address: object) -> None:
        if not isinstance(request, socket.socket):
            super().shutdown_request(request)
            return
        if not self._request_slots.acquire(blocking=False):
            self._reject_busy(request)
            return
        worker = threading.Thread(
            target=self._run_request_worker,
            args=(request, client_address),
            daemon=False,
        )
        try:
            with self._lifecycle_lock:
                closing = self._closing
                if not closing:
                    self._worker_threads.add(worker)
                    worker.start()
        except BaseException:
            with self._lifecycle_lock:
                self._worker_threads.discard(worker)
            self.shutdown_request(request)
            self._request_slots.release()
            raise
        if closing:
            self.shutdown_request(request)
            self._request_slots.release()
            return

    def shutdown_request(self, request: _SocketRequest) -> None:
        if isinstance(request, socket.socket):
            with self._lifecycle_lock:
                self._active_sockets.discard(request)
        super().shutdown_request(request)

    def shutdown(self) -> None:
        self._start_closing()
        super().shutdown()
        self._close_active_sockets()
        self._wait_for_workers()

    def server_close(self) -> None:
        self._start_closing()
        self._close_active_sockets()
        super().server_close()
        self._wait_for_workers()

    def handle_error(self, request: object, client_address: object) -> None:
        """Keep parser and shutdown failures out of local process output."""

    def dispatch_application(self, request: WebRequest) -> WebResponse | None:
        with self._dispatch_lock:
            with self._lifecycle_lock:
                if self._closing:
                    return None
            return self.web_application.handle(request)

    def _run_request_worker(
        self, request: socket.socket, client_address: object
    ) -> None:
        worker = threading.current_thread()
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._lifecycle_lock:
                self._worker_threads.discard(worker)
            self._request_slots.release()

    def _reject_busy(self, request: socket.socket) -> None:
        body = json.dumps(
            {"error": {"code": "server_busy"}, "schema_version": 1},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        headers = [
            b"HTTP/1.1 503 Service Unavailable",
            b"Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(body)}".encode("ascii"),
            b"Connection: close",
        ]
        headers.extend(
            f"{name}: {value}".encode("ascii")
            for name, value in security_headers(api=True)
        )
        response = b"\r\n".join(headers) + b"\r\n\r\n" + body
        with suppress(OSError):
            request.sendall(response)
        self.shutdown_request(request)

    def _start_closing(self) -> None:
        with self._dispatch_lock, self._lifecycle_lock:
            self._closing = True
        self._close_active_sockets()

    def _close_active_sockets(self) -> None:
        with self._lifecycle_lock:
            active = tuple(self._active_sockets)
        for request in active:
            with suppress(OSError):
                request.shutdown(socket.SHUT_RDWR)
            with suppress(OSError):
                request.close()

    def _wait_for_workers(self) -> None:
        current = threading.current_thread()
        while True:
            with self._lifecycle_lock:
                workers = tuple(
                    worker for worker in self._worker_threads if worker is not current
                )
            if not workers:
                return
            for worker in workers:
                worker.join()


class _IPv6LoopbackServer(_LoopbackServer):
    address_family = socket.AF_INET6


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "YTInsights"
    sys_version = ""

    def parse_request(self) -> bool:
        status, raw_target = _inspect_raw_request_line(
            getattr(self, "raw_requestline", b"")
        )
        if status is not None:
            self.requestline = ""
            self.request_version = "HTTP/1.1"
            self.command = ""
            self.close_connection = True
            self.send_error(status)
            return False
        if not super().parse_request():
            return False
        if raw_target is None:
            return True
        self.path = raw_target
        return True

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        """Replace inherited reflecting HTML errors with one fixed JSON shape."""
        status = 405 if code == 501 else code
        if not 400 <= status <= 599:
            status = 400
        if (
            not getattr(self, "request_version", "")
            or self.request_version == "HTTP/0.9"
        ):
            self.request_version = "HTTP/1.0"
        self.close_connection = True
        error_code = "method_not_allowed" if status == 405 else "invalid_request"
        self._error(status, error_code)

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
        try:
            body = self.rfile.read(length)
        except OSError:
            return 400
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
            response = server.dispatch_application(request)
        except Exception:
            self._error(500, "internal_error")
            return
        if response is None:
            self._error(503, "server_shutting_down")
            return
        if not _valid_content_type(response.content_type):
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
        extra_headers: tuple[tuple[object, object], ...] = (),
    ) -> None:
        safe_content_type = (
            content_type
            if _valid_content_type(content_type)
            else "application/octet-stream"
        )
        self.send_response(status)
        self.send_header("Content-Type", safe_content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in security_headers(api=api):
            self.send_header(name, value)
        for extra_name, extra_value in extra_headers:
            if not isinstance(extra_name, str) or not isinstance(extra_value, str):
                continue
            normalized_name = extra_name.lower()
            if (
                normalized_name in _MANAGED_RESPONSE_HEADERS
                or normalized_name.startswith("access-control-")
                or _HEADER_NAME.fullmatch(extra_name) is None
                or "\r" in extra_value
                or "\n" in extra_value
            ):
                continue
            self.send_header(extra_name, extra_value)
        self.end_headers()
        self.wfile.write(body)


def create_server(
    app: WebApplication,
    *,
    host: str,
    port: int,
    static_root: Traversable,
    max_request_workers: int = _DEFAULT_MAX_REQUEST_WORKERS,
) -> ThreadingHTTPServer:
    """Create an inactive exact-loopback server with a frozen static table."""
    validated_host = validate_bind_host(host)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65_535:
        raise ValueError("port must be between 0 and 65535")
    server_class: type[_LoopbackServer] = (
        _IPv6LoopbackServer if validated_host == "::1" else _LoopbackServer
    )
    return server_class(
        (validated_host, port),
        app,
        static_root,
        max_request_workers,
    )


def _build_static_routes(
    static_root: Traversable,
) -> tuple[Mapping[str, _StaticResponse], _StaticResponse | None]:
    if isinstance(static_root, Path):
        return _build_path_static_routes(static_root)
    return _build_generic_static_routes(static_root)


def _build_path_static_routes(
    static_root: Path,
) -> tuple[Mapping[str, _StaticResponse], _StaticResponse | None]:
    routes: dict[str, _StaticResponse] = {}
    root_descriptor = _open_root_directory(static_root)
    if root_descriptor is None:
        return MappingProxyType(routes), None
    try:
        for public_routes, parts in _PAGE_ROUTES:
            response = _read_path_response(
                root_descriptor, parts, "text/html; charset=utf-8"
            )
            if response is None:
                continue
            for public_route in public_routes:
                routes[public_route] = response
        workspace = _read_path_response(
            root_descriptor,
            _WORKSPACE_RESOURCE,
            "text/html; charset=utf-8",
        )
        asset_descriptor = _open_directory_at(root_descriptor, ("_astro",))
        if asset_descriptor is not None:
            try:
                for name in os.listdir(asset_descriptor):
                    content_type = _asset_content_type(name)
                    if content_type is None:
                        continue
                    response = _read_file_at(asset_descriptor, name, content_type)
                    if response is not None:
                        routes[f"/_astro/{name}"] = response
            except OSError:
                pass
            finally:
                os.close(asset_descriptor)
    finally:
        os.close(root_descriptor)
    return MappingProxyType(routes), workspace


def _build_generic_static_routes(
    static_root: Traversable,
) -> tuple[Mapping[str, _StaticResponse], _StaticResponse | None]:
    routes: dict[str, _StaticResponse] = {}
    if not _generic_directory(static_root):
        return MappingProxyType(routes), None
    for public_routes, parts in _PAGE_ROUTES:
        response = _read_generic_response(
            static_root, parts, "text/html; charset=utf-8"
        )
        if response is None:
            continue
        for public_route in public_routes:
            routes[public_route] = response
    workspace = _read_generic_response(
        static_root,
        _WORKSPACE_RESOURCE,
        "text/html; charset=utf-8",
    )
    asset_root = _generic_descendant_directory(static_root, ("_astro",))
    if asset_root is not None:
        try:
            children = tuple(asset_root.iterdir())
        except (AttributeError, OSError, TypeError):
            children = ()
        for child in children:
            try:
                name = child.name
            except (AttributeError, OSError, TypeError):
                continue
            content_type = _asset_content_type(name)
            if content_type is None:
                continue
            response = _read_generic_direct_child(asset_root, child, name, content_type)
            if response is not None:
                routes[f"/_astro/{name}"] = response
    return MappingProxyType(routes), workspace


def _open_root_directory(root: Path) -> int | None:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        return None
    try:
        descriptor = os.open(root, _directory_open_flags())
    except OSError:
        return None
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def _open_directory_at(root_descriptor: int, parts: tuple[str, ...]) -> int | None:
    try:
        current = os.dup(root_descriptor)
    except OSError:
        return None
    for part in parts:
        try:
            child = os.open(part, _directory_open_flags(), dir_fd=current)
        except OSError:
            os.close(current)
            return None
        os.close(current)
        current = child
        try:
            if not stat.S_ISDIR(os.fstat(current).st_mode):
                os.close(current)
                return None
        except OSError:
            os.close(current)
            return None
    return current


def _read_path_response(
    root_descriptor: int,
    parts: tuple[str, ...],
    content_type: str,
) -> _StaticResponse | None:
    parent_descriptor = _open_directory_at(root_descriptor, parts[:-1])
    if parent_descriptor is None:
        return None
    try:
        return _read_file_at(parent_descriptor, parts[-1], content_type)
    finally:
        os.close(parent_descriptor)


def _read_file_at(
    parent_descriptor: int,
    name: str,
    content_type: str,
) -> _StaticResponse | None:
    try:
        descriptor = os.open(name, _file_open_flags(), dir_fd=parent_descriptor)
    except OSError:
        return None
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            return None
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, _READ_CHUNK_BYTES)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            return None
        body = b"".join(chunks)
        if len(body) != before.st_size:
            return None
        return _StaticResponse(body, content_type)
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _directory_open_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_open_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _read_generic_response(
    root: Traversable,
    parts: tuple[str, ...],
    content_type: str,
) -> _StaticResponse | None:
    resource = _generic_descendant_file(root, parts)
    if resource is None:
        return None
    try:
        body = bytes(resource.read_bytes())
    except (AttributeError, OSError, TypeError):
        return None
    return _StaticResponse(body, content_type)


def _generic_descendant_file(
    root: Traversable, parts: tuple[str, ...]
) -> Traversable | None:
    resource = root
    for index, part in enumerate(parts):
        child = _generic_listed_child(resource, part)
        if child is None:
            return None
        resource = child
        try:
            if index < len(parts) - 1 and not resource.is_dir():
                return None
            if index == len(parts) - 1 and not resource.is_file():
                return None
        except (AttributeError, OSError, TypeError):
            return None
    return resource


def _generic_descendant_directory(
    root: Traversable, parts: tuple[str, ...]
) -> Traversable | None:
    resource = root
    for part in parts:
        child = _generic_listed_child(resource, part)
        if child is None:
            return None
        resource = child
        try:
            if not resource.is_dir():
                return None
        except (AttributeError, OSError, TypeError):
            return None
    return resource


def _generic_listed_child(
    parent: Traversable, expected_name: str
) -> Traversable | None:
    try:
        matches = tuple(
            child for child in parent.iterdir() if child.name == expected_name
        )
    except (AttributeError, OSError, TypeError):
        return None
    if len(matches) != 1:
        return None
    return matches[0]


def _read_generic_direct_child(
    parent: Traversable,
    child: Traversable,
    name: str,
    content_type: str,
) -> _StaticResponse | None:
    try:
        joined = parent.joinpath(name)
        if child.name != name or joined.name != name:
            return None
        if not child.is_file() or not joined.is_file():
            return None
        child_bytes = bytes(child.read_bytes())
        joined_bytes = bytes(joined.read_bytes())
    except (AttributeError, OSError, TypeError):
        return None
    if child_bytes != joined_bytes:
        return None
    return _StaticResponse(child_bytes, content_type)


def _generic_directory(resource: Traversable) -> bool:
    try:
        return resource.is_dir()
    except (AttributeError, OSError, TypeError):
        return False


def _asset_content_type(name: str) -> str | None:
    if _ASSET_NAME.fullmatch(name) is None:
        return None
    dot = name.rfind(".")
    if dot <= 0:
        return None
    return _ASSET_CONTENT_TYPES.get(name[dot:].lower())


def _inspect_raw_request_line(raw_requestline: bytes) -> tuple[int | None, str | None]:
    words = raw_requestline.rstrip(b"\r\n").split()
    if len(words) == 2:
        return 400, None
    if len(words) != 3:
        return None, None
    target = words[1]
    if len(target) > _MAX_TARGET_BYTES:
        return 414, None
    if target.startswith(b"//"):
        return 400, None
    try:
        return None, target.decode("ascii")
    except UnicodeDecodeError:
        return 400, None


def _valid_content_type(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and _CONTENT_TYPE.fullmatch(value) is not None
    )
