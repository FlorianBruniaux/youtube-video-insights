"""LLM backend auto-detection and resolution.

Detection order (first match wins):
  1. config.base_url explicitly set (not the default) -> OpenAICompatBackend direct
  2. cc-bridge on http://127.0.0.1:4141 -> OpenAICompatBackend on :4141/v1
  3. Ollama on http://127.0.0.1:11434   -> OpenAICompatBackend on :11434/v1
  4. ANTHROPIC_API_KEY env var           -> OpenAICompatBackend on api.anthropic.com
  5. BackendNotFoundError

resolve_backend() is lazy: it must be called before the first LLM call, not at
import time, so the module can be imported in tests without triggering network probes.
"""

from __future__ import annotations

import os

import httpx

from .base import BackendNotFoundError, LLMBackend
from .openai_compat import OpenAICompatBackend
from ..config import Config, DEFAULT_BASE_URL

_CC_BRIDGE = "http://127.0.0.1:4141"
_OLLAMA = "http://127.0.0.1:11434"


def resolve_backend(config: Config) -> LLMBackend:
    """Auto-detect and return a ready-to-use LLM backend."""

    # Explicit base_url set by user -> use it directly, no probing
    if config.base_url != DEFAULT_BASE_URL:
        return OpenAICompatBackend(config)

    # Probe cc-bridge
    try:
        with httpx.Client(timeout=1.0) as c:
            r = c.get(f"{_CC_BRIDGE}/health")
        if r.status_code == 200:
            return OpenAICompatBackend(config.with_url(f"{_CC_BRIDGE}/v1", api_key="local"))
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    # Probe Ollama
    try:
        with httpx.Client(timeout=1.0) as c:
            r = c.get(f"{_OLLAMA}/api/tags")
        if r.status_code == 200:
            models = [m["name"] for m in r.json().get("models", [])]
            if models:
                preferred = next(
                    (m for m in models if "llama" in m.lower() or "qwen" in m.lower()),
                    models[0],
                )
                return OpenAICompatBackend(
                    config.with_url(f"{_OLLAMA}/v1", model=preferred)
                )
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    # Anthropic API key in env
    if key := os.getenv("ANTHROPIC_API_KEY"):
        return OpenAICompatBackend(
            config.with_url("https://api.anthropic.com/v1", api_key=key)
        )

    raise BackendNotFoundError(
        "No LLM backend found. Options:\n"
        "  - Start Ollama: ollama serve\n"
        "  - Start cc-bridge on port 4141\n"
        "  - Set ANTHROPIC_API_KEY environment variable\n"
        "  - Configure ~/.config/yt-insights/config.toml with base_url and api_key"
    )


def backend_type(backend: LLMBackend) -> str:
    """Return a short type string for concurrency tuning."""
    name = type(backend).__name__.lower()
    if "mlx" in name:
        return "mlx"
    cfg = getattr(backend, "_config", None)
    if cfg and "11434" in getattr(cfg, "base_url", ""):
        return "ollama"
    return "api"


__all__ = [
    "resolve_backend",
    "backend_type",
    "LLMBackend",
    "BackendNotFoundError",
]
