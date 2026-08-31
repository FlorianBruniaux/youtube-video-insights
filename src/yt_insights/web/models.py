"""Small immutable transport records for the local web application."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import parse_qs


@dataclass(frozen=True, slots=True)
class WebRequest:
    """A framework-neutral request with repeated query values preserved."""

    method: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    headers: Mapping[str, str]
    body: bytes = b""

    def __post_init__(self) -> None:
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("request method must be a non-empty string")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ValueError("request path must be absolute")
        if not isinstance(self.body, bytes):
            raise TypeError("request body must be bytes")
        normalized_query: dict[str, tuple[str, ...]] = {}
        for key, values in self.query.items():
            if not isinstance(key, str) or not isinstance(values, tuple):
                raise TypeError("request query is invalid")
            if not all(isinstance(value, str) for value in values):
                raise TypeError("request query values must be strings")
            normalized_query[key] = values
        normalized_headers: dict[str, str] = {}
        for key, value in self.headers.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("request headers must be strings")
            normalized_headers[key] = value
        object.__setattr__(self, "method", self.method.upper())
        object.__setattr__(self, "query", MappingProxyType(normalized_query))
        object.__setattr__(self, "headers", MappingProxyType(normalized_headers))

    @classmethod
    def get(cls, path: str, query: str = "") -> WebRequest:
        """Build a GET request without collapsing repeated scalar parameters."""
        parsed = parse_qs(query, keep_blank_values=True, strict_parsing=False)
        return cls(
            "GET",
            path,
            {key: tuple(values) for key, values in parsed.items()},
            {},
        )


@dataclass(frozen=True, slots=True)
class WebResponse:
    """A framework-neutral response body."""

    status: int
    body: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.status, bool) or not isinstance(self.status, int):
            raise TypeError("response status must be an integer")
        if not 100 <= self.status <= 599:
            raise ValueError("response status is invalid")
        if not isinstance(self.body, bytes):
            raise TypeError("response body must be bytes")

    @classmethod
    def json(cls, status: int, payload: Mapping[str, object]) -> WebResponse:
        """Serialize one finite JSON object with deterministic key order."""
        body = json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls(status, body)

    @property
    def json_body(self) -> dict[str, object]:
        """Decode the response for adapter tests and framework bridges."""
        payload = json.loads(self.body)
        if not isinstance(payload, dict):
            raise TypeError("response JSON must be an object")
        return payload
