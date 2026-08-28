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

import importlib.util
import os
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

import httpx

from .base import BackendNotFoundError, LLMBackend
from .mlx import MLXBackend
from .openai_compat import OpenAICompatBackend
from ..config import Config, DEFAULT_BASE_URL, DEFAULT_MODEL

_CC_BRIDGE = "http://127.0.0.1:4141"
_OLLAMA = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class BackendIdentity:
    """Public, resolved backend metadata safe to display to users."""

    backend: str
    endpoint: str
    model: str


class ResolvedBackend:
    """A backend plus its public, runtime-resolved identity."""

    def __init__(self, backend: LLMBackend, identity: BackendIdentity) -> None:
        self._backend = backend
        self.identity = identity

    def generate(self, prompt: str, *, max_tokens: int, timeout: int) -> tuple[str, str]:
        return self._backend.generate(prompt, max_tokens=max_tokens, timeout=timeout)

    def stream(self, prompt: str, *, max_tokens: int, timeout: int) -> Iterator[str]:
        return self._backend.stream(prompt, max_tokens=max_tokens, timeout=timeout)

    def close(self) -> None:
        self._backend.close()


def format_backend_identity(identity: BackendIdentity) -> str:
    """Format the public runtime identity consistently for CLI entry points."""
    endpoint = sanitize_endpoint(identity.endpoint)
    return f"backend={identity.backend} endpoint={endpoint} model={identity.model}"


def sanitize_endpoint(endpoint: str) -> str:
    """Return a useful endpoint label without credentials or URL parameters."""
    if endpoint == "local://mlx":
        return endpoint
    try:
        if any(character.isspace() or ord(character) < 32 for character in endpoint):
            return "<invalid-endpoint>"
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<invalid-endpoint>"
        port = parsed.port
        host = parsed.hostname
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, "", ""))
    except (TypeError, ValueError, UnicodeError):
        return "<invalid-endpoint>"


def _resolved(config: Config, backend: str) -> ResolvedBackend:
    return ResolvedBackend(
        OpenAICompatBackend(config),
        BackendIdentity(
            backend=backend,
            endpoint=sanitize_endpoint(config.base_url),
            model=config.model,
        ),
    )


def _resolved_mlx(config: Config) -> ResolvedBackend:
    return ResolvedBackend(
        MLXBackend(config),
        BackendIdentity(
            backend="mlx",
            endpoint="local://mlx",
            model=config.model,
        ),
    )


def _parse_ollama_models(response: httpx.Response) -> list[str]:
    try:
        payload = response.json()
    except (ValueError, httpx.DecodingError) as exc:
        raise BackendNotFoundError("Ollama returned an invalid /api/tags response. Check Ollama and retry.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        raise BackendNotFoundError("Ollama returned an invalid /api/tags response. Check Ollama and retry.")
    models: list[str] = []
    for item in payload["models"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise BackendNotFoundError("Ollama returned an invalid /api/tags response. Check Ollama and retry.")
        models.append(item["name"])
    return models


def _ollama_backend(config: Config, endpoint: str, *, explicit: bool) -> ResolvedBackend | None:
    try:
        with httpx.Client(timeout=1.0) as client:
            response = client.get(f"{endpoint}/api/tags")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        if explicit:
            raise BackendNotFoundError(
                f"Cannot reach explicitly configured Ollama at {sanitize_endpoint(endpoint)}. "
                "Start it with: ollama serve"
            ) from exc
        return None
    if response.status_code != 200:
        if explicit:
            raise BackendNotFoundError(
                f"Ollama at {sanitize_endpoint(endpoint)} returned HTTP "
                f"{response.status_code} for /api/tags."
            )
        return None
    try:
        models = _parse_ollama_models(response)
    except BackendNotFoundError:
        if explicit:
            raise
        return None
    if config.model_source != "default":
        if config.model not in models:
            available = ", ".join(models[:8]) or "(none)"
            remainder = "" if len(models) <= 8 else f" (+{len(models) - 8} more)"
            raise BackendNotFoundError(
                f"Ollama is running, but the requested model '{config.model}' is not installed. "
                f"Available models: {available}{remainder}. Install it with: ollama pull {config.model}"
            )
        model = config.model
        model_source = config.model_source
    elif models:
        model = next((name for name in models if "llama" in name.lower() or "qwen" in name.lower()), models[0])
        model_source = "detected"
    else:
        if not explicit:
            return None
        raise BackendNotFoundError(
            f"Ollama at {sanitize_endpoint(endpoint)} has no installed models. "
            "Install one with: ollama pull llama3.2"
        )
    return _resolved(
        config.with_url(f"{endpoint}/v1", model=model, model_source=model_source, base_url_source="detected"),
        "ollama",
    )


def _ollama_endpoint(base_url: str) -> str | None:
    """Return the credential-free Ollama probe origin for an explicit URL."""
    try:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.port != 11434
            or parsed.path.rstrip("/") != "/v1"
        ):
            return None
    except (TypeError, ValueError, UnicodeError):
        return None
    return sanitize_endpoint(base_url).rstrip("/").removesuffix("/v1")


def _probe_llm(cfg: "Config") -> bool:
    """Send a 1-token completion to verify the LLM endpoint actually responds."""
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.post(
                f"{cfg.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {cfg.api_key}", "x-api-key": cfg.api_key or ""},
                json={"model": cfg.model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
            )
        return 200 <= r.status_code < 400
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _cc_bridge_backend(config: Config, *, explicit: bool) -> ResolvedBackend | None:
    try:
        with httpx.Client(timeout=1.0) as client:
            response = client.get(f"{_CC_BRIDGE}/health")
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        if explicit:
            raise BackendNotFoundError(
                "Cannot reach explicitly configured cc-bridge at "
                f"{_CC_BRIDGE}. Start cc-bridge and retry."
            ) from exc
        return None
    if response.status_code != 200:
        if explicit:
            raise BackendNotFoundError(
                f"Explicitly configured cc-bridge returned HTTP {response.status_code} "
                "for /health."
            )
        return None
    bridge_config = config.with_url(f"{_CC_BRIDGE}/v1", api_key="local")
    if not _probe_llm(bridge_config):
        if explicit:
            raise BackendNotFoundError(
                "Explicitly configured cc-bridge is healthy but its completion "
                "route is unusable."
            )
        return None
    return _resolved(bridge_config, "cc-bridge")


def _explicit_backend(config: Config) -> ResolvedBackend:
    if config.backend == "mlx":
        if not config.model.strip() or config.model == DEFAULT_MODEL:
            raise BackendNotFoundError(
                "Explicit MLX requires an MLX model name, for example "
                "mlx-community/Qwen3-4B."
            )
        return _resolved_mlx(config)

    if config.backend == "ollama":
        if config.base_url == DEFAULT_BASE_URL:
            endpoint = _OLLAMA
        else:
            endpoint = _ollama_endpoint(config.base_url)
            if endpoint is None:
                raise BackendNotFoundError(
                    "Explicit Ollama requires an endpoint shaped like "
                    "http://HOST:11434/v1."
                )
        return _ollama_backend(config, endpoint, explicit=True)  # type: ignore[return-value]

    if config.backend == "cc-bridge":
        if config.base_url != DEFAULT_BASE_URL and sanitize_endpoint(
            config.base_url
        ) != f"{_CC_BRIDGE}/v1":
            raise BackendNotFoundError(
                f"Explicit cc-bridge uses the fixed local endpoint {_CC_BRIDGE}/v1."
            )
        return _cc_bridge_backend(config, explicit=True)  # type: ignore[return-value]

    if config.backend == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY") or config.api_key
        if not key:
            raise BackendNotFoundError(
                "Explicit Anthropic requires ANTHROPIC_API_KEY or api_key in config."
            )
        return _resolved(
            config.with_url(
                DEFAULT_BASE_URL,
                api_key=key,
                base_url_source="explicit-backend",
            ),
            "anthropic",
        )

    if config.backend == "openai":
        if config.base_url == DEFAULT_BASE_URL:
            raise BackendNotFoundError(
                "Explicit OpenAI requires a configured non-default base_url."
            )
        endpoint = sanitize_endpoint(config.base_url)
        if endpoint == "<invalid-endpoint>":
            raise BackendNotFoundError(
                "Explicit OpenAI requires a valid HTTP(S) base_url."
            )
        if not config.model.strip() or config.model == DEFAULT_MODEL:
            raise BackendNotFoundError(
                "Explicit OpenAI requires an explicit non-default model."
            )
        if endpoint == f"{_CC_BRIDGE}/v1" or _ollama_endpoint(config.base_url) is not None:
            raise BackendNotFoundError(
                "A reserved local endpoint cannot be selected as the explicit OpenAI route."
            )
        return _resolved(config, "openai")

    raise BackendNotFoundError(f"Unsupported explicit backend: {config.backend}")


def available_backend_routes(config: Config) -> tuple[str, ...]:
    """Return only configured or cheaply detected routes, without model calls."""
    if config.backend != "auto":
        return (config.backend,)

    routes: list[str] = []
    try:
        with httpx.Client(timeout=1.0) as client:
            response = client.get(f"{_CC_BRIDGE}/health")
        if response.status_code == 200:
            routes.append("cc-bridge")
    except (httpx.ConnectError, httpx.TimeoutException):
        pass

    try:
        with httpx.Client(timeout=1.0) as client:
            response = client.get(f"{_OLLAMA}/api/tags")
        if response.status_code == 200:
            models = _parse_ollama_models(response)
            if models and (
                config.model_source == "default" or config.model in models
            ):
                routes.append("ollama")
    except (BackendNotFoundError, httpx.ConnectError, httpx.TimeoutException):
        pass

    try:
        mlx_available = importlib.util.find_spec("mlx_lm") is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        mlx_available = False
    if (
        mlx_available
        and config.model_source != "default"
        and config.model.strip()
        and config.model != DEFAULT_MODEL
    ):
        routes.append("mlx")

    if os.getenv("ANTHROPIC_API_KEY") or config.api_key:
        routes.append("anthropic")

    configured_endpoint = sanitize_endpoint(config.base_url)
    if (
        config.base_url != DEFAULT_BASE_URL
        and configured_endpoint != "<invalid-endpoint>"
        and configured_endpoint != f"{_CC_BRIDGE}/v1"
        and _ollama_endpoint(config.base_url) is None
        and config.model_source != "default"
    ):
        routes.append("openai")

    return tuple(routes)


def resolve_backend(config: Config) -> ResolvedBackend:
    """Auto-detect and return a ready-to-use LLM backend."""

    if config.backend != "auto":
        return _explicit_backend(config)

    # An explicit Ollama endpoint must verify the requested model before use.
    if endpoint := _ollama_endpoint(config.base_url):
        return _ollama_backend(config, endpoint, explicit=True)  # type: ignore[return-value]

    # Explicit non-Ollama base_url set by user -> use it directly, no probing
    if config.base_url != DEFAULT_BASE_URL:
        return _resolved(config, "api")

    # Probe cc-bridge: health check + minimal LLM call to detect 502 upstreams
    cc_bridge = _cc_bridge_backend(config, explicit=False)
    if cc_bridge is not None:
        return cc_bridge

    # Probe Ollama
    ollama_error: BackendNotFoundError | None = None
    try:
        ollama = _ollama_backend(config, _OLLAMA, explicit=False)
    except BackendNotFoundError as exc:
        # A selected model is not the same as a selected endpoint. Keep the
        # actionable local error only if no cloud fallback is available.
        ollama = None
        ollama_error = exc
    if ollama is not None:
        return ollama

    # Anthropic API key in env
    if key := os.getenv("ANTHROPIC_API_KEY") or config.api_key:
        return _resolved(config.with_url("https://api.anthropic.com/v1", api_key=key, base_url_source="detected"), "anthropic")

    if ollama_error is not None:
        raise ollama_error

    raise BackendNotFoundError(
        "No LLM backend found. Options:\n"
        "  - Start Ollama: ollama serve\n"
        "  - Start cc-bridge on port 4141\n"
        "  - Set ANTHROPIC_API_KEY environment variable\n"
        "  - Configure ~/.config/yt-insights/config.toml with base_url and api_key"
    )


def backend_type(backend: LLMBackend) -> str:
    """Return a short type string for concurrency tuning."""
    identity = getattr(backend, "identity", None)
    if identity is not None:
        return identity.backend
    return "api"


__all__ = [
    "resolve_backend",
    "backend_type",
    "LLMBackend",
    "BackendNotFoundError",
    "BackendIdentity",
    "ResolvedBackend",
    "format_backend_identity",
    "sanitize_endpoint",
    "available_backend_routes",
]
