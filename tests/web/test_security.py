from __future__ import annotations

from yt_insights.web.security import security_headers


def test_static_security_headers_confine_content_without_enabling_cors() -> None:
    """Removing a confinement directive would let packaged pages widen their origin."""
    headers = dict(security_headers(api=False))

    policy = headers["Content-Security-Policy"]
    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "connect-src 'self'" in policy
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Permissions-Policy"] == (
        "accelerometer=(), autoplay=(), camera=(), geolocation=(), "
        "gyroscope=(), microphone=(), payment=(), usb=()"
    )
    assert "Cache-Control" not in headers
    assert not any(name.lower().startswith("access-control-") for name in headers)


def test_api_security_headers_prevent_storage() -> None:
    """Caching API or bootstrap data could retain the process mutation token."""
    headers = dict(security_headers(api=True))

    assert headers["Cache-Control"] == "no-store"
    assert headers["X-Content-Type-Options"] == "nosniff"
