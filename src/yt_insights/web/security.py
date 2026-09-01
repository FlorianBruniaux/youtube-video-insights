"""Security policy shared by the loopback HTTP transport."""

from __future__ import annotations

import hmac

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
MUTATION_TOKEN_HEADER = "X-YT-Insights-Token"

_CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "connect-src 'self'",
        "form-action 'self'",
    )
)
_PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(), camera=(), geolocation=(), "
    "gyroscope=(), microphone=(), payment=(), usb=()"
)


def security_headers(*, api: bool) -> tuple[tuple[str, str], ...]:
    """Return the fixed browser policy for API or packaged static content."""
    headers = (
        ("Content-Security-Policy", _CONTENT_SECURITY_POLICY),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("Permissions-Policy", _PERMISSIONS_POLICY),
    )
    if api:
        return (*headers, ("Cache-Control", "no-store"))
    return headers


def validate_bind_host(host: str) -> str:
    """Reject aliases and wildcard addresses before opening a listener."""
    if host not in LOOPBACK_HOSTS:
        raise ValueError("host must be an exact loopback address")
    return host


def expected_host_header(host: str, port: int) -> str:
    """Format the one Host value accepted by the active listener."""
    if host == "::1":
        return f"[::1]:{port}"
    return f"127.0.0.1:{port}"


def mutation_token_matches(values: list[str], expected: str) -> bool:
    """Require one exact token value without reflecting either value."""
    if len(values) != 1:
        return False
    try:
        supplied_bytes = values[0].encode("ascii")
        expected_bytes = expected.encode("ascii")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied_bytes, expected_bytes)
