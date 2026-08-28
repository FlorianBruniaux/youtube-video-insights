from __future__ import annotations

import sys
from types import SimpleNamespace

import httpx
import pytest

from yt_insights import backends
from yt_insights.backends import (
    BackendIdentity,
    BackendNotFoundError,
    format_backend_identity,
    resolve_backend,
)
from yt_insights.backends.mlx import MLXBackend
from yt_insights.config import DEFAULT_MODEL, Config, load_config

OLLAMA_URL = "http://127.0.0.1:11434/v1"


class _Response:
    def __init__(self, status_code: int, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


class _ProbeClient:
    def __init__(self, tags_payload: object) -> None:
        self._tags_payload = tags_payload

    def __enter__(self) -> "_ProbeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str) -> _Response:
        if url.endswith("/health"):
            raise httpx.ConnectError("cc-bridge is unavailable")
        if url.endswith("/api/tags"):
            return _Response(200, self._tags_payload)
        raise AssertionError(f"Unexpected probe URL: {url}")

    def close(self) -> None:
        return None


def _mock_probes(monkeypatch: pytest.MonkeyPatch, tags_payload: object) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _ProbeClient(tags_payload),
    )


def test_ollama_uses_the_exact_configured_model_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects accidental substitution of a requested Ollama model."""
    _mock_probes(
        monkeypatch,
        {"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]},
    )

    backend = resolve_backend(Config(model="qwen3:8b", model_source="cli"))

    try:
        assert backend.identity.backend == "ollama"
        assert backend.identity.endpoint == "http://127.0.0.1:11434/v1"
        assert backend.identity.model == "qwen3:8b"
    finally:
        backend.close()


def test_auto_detection_falls_back_to_anthropic_when_ollama_lacks_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model choice alone must not turn Ollama into a mandatory endpoint."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    backend = resolve_backend(Config(model="qwen3:8b", model_source="cli"))

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == "qwen3:8b"
    finally:
        backend.close()


def test_default_config_preserves_automatic_ollama_model_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects treating the default Anthropic model as an Ollama requirement."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})

    backend = resolve_backend(Config())

    try:
        assert backend.identity.backend == "ollama"
        assert backend.identity.model == "llama3.2:latest"
    finally:
        backend.close()


def test_explicit_ollama_endpoint_validates_an_absent_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects an explicit Ollama endpoint bypassing its model inventory."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})

    with pytest.raises(BackendNotFoundError, match="missing:tag") as exc_info:
        resolve_backend(
            Config(
                base_url="http://127.0.0.1:11434/v1",
                model="missing:tag",
                base_url_source="cli",
                model_source="cli",
            )
        )

    assert "ollama pull missing:tag" in str(exc_info.value)


@pytest.mark.parametrize("payload", [[], {"models": None}, {"models": ["llama"]}])
def test_invalid_ollama_tags_response_fails_actionably(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    """Detects a malformed local inventory escaping as a raw parser exception."""
    _mock_probes(monkeypatch, payload)  # type: ignore[arg-type]

    with pytest.raises(BackendNotFoundError, match="invalid /api/tags response"):
        resolve_backend(
            Config(
                base_url=OLLAMA_URL,
                base_url_source="cli",
                model="qwen3:8b",
                model_source="cli",
            )
        )


def test_direct_config_model_is_an_exact_ollama_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects losing direct caller intent behind the default source marker."""
    _mock_probes(
        monkeypatch,
        {"models": [{"name": "llama3.2:latest"}, {"name": "qwen3:8b"}]},
    )

    backend = resolve_backend(Config(model="qwen3:8b"))

    try:
        assert backend.identity.model == "qwen3:8b"
    finally:
        backend.close()


def test_implicit_malformed_ollama_tags_falls_back_to_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects a broken unselected local probe blocking cloud fallback."""
    _mock_probes(monkeypatch, {"models": None})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    backend = resolve_backend(Config())

    try:
        assert backend.identity.backend == "anthropic"
    finally:
        backend.close()


def test_explicit_model_with_malformed_auto_detected_ollama_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only an explicit Ollama endpoint makes a bad Ollama probe fatal."""
    _mock_probes(monkeypatch, {"models": None})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    backend = resolve_backend(Config(model="qwen3:8b"))

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == "qwen3:8b"
    finally:
        backend.close()


def test_direct_default_model_selects_its_exact_ollama_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects substituting a preferred local model for an explicit default tag."""
    _mock_probes(
        monkeypatch,
        {"models": [{"name": "llama3.2:latest"}, {"name": DEFAULT_MODEL}]},
    )

    backend = resolve_backend(Config(model=DEFAULT_MODEL))

    try:
        assert backend.identity.model == DEFAULT_MODEL
    finally:
        backend.close()


def test_direct_default_model_missing_from_ollama_uses_anthropic_without_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cloud fallback must preserve even an explicit value equal to the default."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    backend = resolve_backend(Config(model=DEFAULT_MODEL))

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == DEFAULT_MODEL
    finally:
        backend.close()


def test_with_url_default_model_selects_its_exact_ollama_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects replacing an explicit with_url model with an auto-selected tag."""
    _mock_probes(
        monkeypatch,
        {"models": [{"name": "llama3.2:latest"}, {"name": DEFAULT_MODEL}]},
    )

    backend = resolve_backend(Config().with_url(OLLAMA_URL, model=DEFAULT_MODEL))

    try:
        assert backend.identity.model == DEFAULT_MODEL
    finally:
        backend.close()


def test_with_url_default_model_missing_from_ollama_fails_actionably(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detects substituting another tag when a with_url model is unavailable."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})

    with pytest.raises(BackendNotFoundError, match=DEFAULT_MODEL) as exc_info:
        resolve_backend(Config().with_url(OLLAMA_URL, model=DEFAULT_MODEL))

    assert f"ollama pull {DEFAULT_MODEL}" in str(exc_info.value)


def test_auto_detection_without_cloud_key_reports_missing_ollama_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keeps an actionable local error when no compatible fallback exists."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(BackendNotFoundError, match="qwen3:8b") as exc_info:
        resolve_backend(Config(model="qwen3:8b", model_source="env"))

    assert "ollama pull qwen3:8b" in str(exc_info.value)


def test_env_model_provenance_does_not_make_ollama_mandatory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protects the real environment configuration path, not only direct Config calls."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.setenv("YT_INSIGHTS_MODEL", "cloud-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    backend = resolve_backend(load_config({}))

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == "cloud-model"
    finally:
        backend.close()


def test_toml_model_provenance_does_not_make_ollama_mandatory(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protects the config-file path from coupling model and endpoint intent."""
    from yt_insights import config as config_module

    config_path = tmp_path / "config.toml"
    config_path.write_text('model = "cloud-model"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    backend = resolve_backend(load_config({}))

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == "cloud-model"
    finally:
        backend.close()


def test_configured_anthropic_api_key_is_available_for_model_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A key already merged into Config must work without a second env variable."""
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    backend = resolve_backend(
        Config(model="cloud-model", model_source="toml", api_key="configured-key")
    )

    try:
        assert backend.identity.backend == "anthropic"
        assert backend.identity.model == "cloud-model"
    finally:
        backend.close()


def test_explicit_ollama_url_with_secrets_stays_strict_without_reflecting_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URL decorations must not bypass explicit Ollama validation or leak in errors."""
    _mock_probes(monkeypatch, {"models": None})
    endpoint = (
        "http://alice:password-secret@127.0.0.1:11434/v1"
        "?token=query-secret#fragment-secret"
    )

    with pytest.raises(BackendNotFoundError) as exc_info:
        resolve_backend(
            Config(
                base_url=endpoint,
                base_url_source="cli",
                model="qwen3:8b",
                model_source="cli",
            )
        )

    rendered = str(exc_info.value)
    assert "invalid /api/tags response" in rendered
    assert "password-secret" not in rendered
    assert "query-secret" not in rendered
    assert "fragment-secret" not in rendered


def test_backend_identity_redacts_endpoint_credentials_query_and_fragment() -> None:
    """The public identity must never echo transport credentials or URL secrets."""
    identity = BackendIdentity(
        "api",
        "https://alice:super-secret@example.test:8443/v1/chat?api_key=query-secret#fragment-secret",
        "safe-model",
    )

    rendered = format_backend_identity(identity)

    assert rendered == "backend=api endpoint=https://example.test:8443/v1/chat model=safe-model"
    assert "super-secret" not in rendered
    assert "query-secret" not in rendered
    assert "fragment-secret" not in rendered


@pytest.mark.parametrize(
    "endpoint",
    [
        "not a URL secret-token",
        "https://[invalid secret-token",
        "https://example.test secret-token/v1",
        "file:///tmp/secret-token",
    ],
)
def test_backend_identity_replaces_invalid_endpoint_with_generic_value(endpoint: str) -> None:
    rendered = format_backend_identity(BackendIdentity("api", endpoint, "safe-model"))

    assert "endpoint=<invalid-endpoint>" in rendered
    assert "secret-token" not in rendered


class _CcBridgeClient:
    def __init__(self, status_code: int, tags_payload: object) -> None:
        self.status_code = status_code
        self.tags_payload = tags_payload

    def __enter__(self) -> "_CcBridgeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def get(self, url: str) -> _Response:
        if url.endswith("/health"):
            return _Response(200)
        if url.endswith("/api/tags"):
            return _Response(200, self.tags_payload)
        raise AssertionError(f"Unexpected GET URL: {url}")

    def post(self, url: str, **kwargs: object) -> _Response:
        assert url.endswith("/chat/completions")
        return _Response(self.status_code)


@pytest.mark.parametrize("status_code", [401, 403, 404, 429, 500])
def test_cc_bridge_unusable_completion_status_falls_back_to_ollama(
    status_code: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A healthy bridge endpoint is not usable when its completion request is rejected."""
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _CcBridgeClient(status_code, {"models": [{"name": "qwen3:8b"}]}),
    )

    backend = resolve_backend(Config(model="qwen3:8b", model_source="cli"))

    try:
        assert backend.identity.backend == "ollama"
    finally:
        backend.close()


@pytest.mark.parametrize("status_code", [200, 204, 302, 399])
def test_cc_bridge_success_or_redirect_completion_status_is_accepted(
    status_code: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe accepts only HTTP success and redirect classes."""
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _CcBridgeClient(status_code, {"models": []}),
    )

    backend = resolve_backend(Config(model="requested-model", model_source="cli"))

    try:
        assert backend.identity.backend == "cc-bridge"
        assert backend.identity.model == "requested-model"
    finally:
        backend.close()


class _FakeMLXBackend:
    def __init__(self, config: Config) -> None:
        self.config = config

    def generate(self, prompt: str, *, max_tokens: int, timeout: int) -> tuple[str, str]:
        return prompt, "end_turn"

    def stream(self, prompt: str, *, max_tokens: int, timeout: int):
        yield prompt

    def close(self) -> None:
        return None


def test_explicit_mlx_never_probes_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("explicit MLX performed an HTTP probe"),
    )
    monkeypatch.setattr(backends, "MLXBackend", _FakeMLXBackend)

    resolved = resolve_backend(
        Config(backend="mlx", model="mlx-community/Qwen3-4B")
    )

    try:
        assert resolved.identity == BackendIdentity(
            backend="mlx",
            endpoint="local://mlx",
            model="mlx-community/Qwen3-4B",
        )
    finally:
        resolved.close()


@pytest.mark.parametrize("model", ["", DEFAULT_MODEL])
def test_explicit_mlx_rejects_an_empty_or_default_cloud_model(model: str) -> None:
    with pytest.raises(BackendNotFoundError, match="MLX model"):
        resolve_backend(Config(backend="mlx", model=model))


def test_mlx_loads_model_once_and_uses_the_public_generate_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_calls: list[str] = []
    generate_calls: list[tuple[object, object, str, int, bool]] = []
    model = object()
    tokenizer = object()

    def fake_load(model_name: str) -> tuple[object, object]:
        load_calls.append(model_name)
        return model, tokenizer

    def fake_generate(
        loaded_model: object,
        loaded_tokenizer: object,
        *,
        prompt: str,
        max_tokens: int,
        verbose: bool,
    ) -> str:
        generate_calls.append(
            (loaded_model, loaded_tokenizer, prompt, max_tokens, verbose)
        )
        return f" answer to {prompt} "

    monkeypatch.setitem(
        sys.modules,
        "mlx_lm",
        SimpleNamespace(load=fake_load, generate=fake_generate),
    )
    backend = MLXBackend(Config(backend="mlx", model="test-model"))

    assert backend.generate("one", max_tokens=8, timeout=10) == (
        "answer to one",
        "end_turn",
    )
    assert backend.generate("two", max_tokens=9, timeout=10) == (
        "answer to two",
        "end_turn",
    )
    assert load_calls == ["test-model"]
    assert generate_calls == [
        (model, tokenizer, "one", 8, False),
        (model, tokenizer, "two", 9, False),
    ]


def test_explicit_ollama_backend_validates_the_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_probes(monkeypatch, {"models": [{"name": "llama3.2:latest"}]})

    with pytest.raises(BackendNotFoundError, match="missing:tag") as exc_info:
        resolve_backend(Config(backend="ollama", model="missing:tag"))

    assert "ollama pull missing:tag" in str(exc_info.value)


def test_explicit_anthropic_requires_a_configured_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("missing key reached HTTP setup"),
    )

    with pytest.raises(BackendNotFoundError, match="ANTHROPIC_API_KEY"):
        resolve_backend(Config(backend="anthropic", api_key=""))


def test_explicit_anthropic_never_reuses_a_custom_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(BackendNotFoundError, match="ANTHROPIC_API_KEY"):
        resolve_backend(
            Config(
                backend="anthropic",
                base_url="https://gateway.example.test/v1",
                api_key="gateway-only-key",
            )
        )


def test_explicit_anthropic_environment_key_overrides_a_custom_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_keys: list[str] = []

    class _CapturingBackend(_FakeMLXBackend):
        def __init__(self, config: Config) -> None:
            super().__init__(config)
            observed_keys.append(config.api_key)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-env-key")
    monkeypatch.setattr(backends, "OpenAICompatBackend", _CapturingBackend)

    resolved = resolve_backend(
        Config(
            backend="anthropic",
            base_url="https://gateway.example.test/v1",
            api_key="gateway-only-key",
        )
    )

    try:
        assert resolved.identity.backend == "anthropic"
        assert observed_keys == ["anthropic-env-key"]
    finally:
        resolved.close()


def test_backend_discovery_never_lists_anthropic_from_a_custom_gateway_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnavailableClient:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Response:
            raise httpx.ConnectError("unavailable")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(backends.httpx, "Client", lambda timeout: _UnavailableClient())
    monkeypatch.setattr(backends.importlib.util, "find_spec", lambda name: None)

    routes = backends.available_backend_routes(
        Config(
            base_url="https://gateway.example.test/v1",
            api_key="gateway-only-key",
            model="gateway-model",
        )
    )

    assert routes == ("openai",)


def test_auto_backend_keeps_a_custom_gateway_key_on_the_custom_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    resolved = resolve_backend(
        Config(
            base_url="https://gateway.example.test/v1",
            api_key="gateway-only-key",
            model="gateway-model",
        )
    )

    try:
        assert resolved.identity.backend == "api"
        assert resolved.identity.endpoint == "https://gateway.example.test/v1"
    finally:
        resolved.close()


def test_explicit_openai_fails_closed_without_a_configured_endpoint() -> None:
    with pytest.raises(BackendNotFoundError, match="base_url"):
        resolve_backend(Config(backend="openai", model="gpt-test"))


@pytest.mark.parametrize(
    "endpoint",
    ["not-an-http-endpoint", "http://127.0.0.1:4141/v1"],
)
def test_explicit_openai_rejects_invalid_or_reserved_endpoints(endpoint: str) -> None:
    with pytest.raises(BackendNotFoundError, match="OpenAI"):
        resolve_backend(
            Config(backend="openai", base_url=endpoint, model="gpt-test")
        )


def test_explicit_openai_uses_a_configured_endpoint_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeOpenAIBackend(_FakeMLXBackend):
        pass

    monkeypatch.setattr(backends, "OpenAICompatBackend", _FakeOpenAIBackend)
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("explicit OpenAI performed a probe"),
    )

    resolved = resolve_backend(
        Config(
            backend="openai",
            base_url="https://gateway.example.test/v1",
            model="gpt-test",
        )
    )

    try:
        assert resolved.identity.backend == "openai"
        assert resolved.identity.endpoint == "https://gateway.example.test/v1"
        assert resolved.identity.model == "gpt-test"
    finally:
        resolved.close()


def test_explicit_cc_bridge_fails_closed_when_completion_route_is_unusable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _CcBridgeClient(502, {"models": []}),
    )

    with pytest.raises(BackendNotFoundError, match="cc-bridge"):
        resolve_backend(Config(backend="cc-bridge", model="route/model"))


def test_auto_backend_keeps_cc_bridge_before_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _CcBridgeClient(
            200, {"models": [{"name": "ollama-would-have-won"}]}
        ),
    )

    resolved = resolve_backend(Config(backend="auto", model="route/model"))

    try:
        assert resolved.identity.backend == "cc-bridge"
    finally:
        resolved.close()


def test_backend_route_discovery_never_loads_mlx_or_calls_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DiscoveryClient:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Response:
            if url.endswith("/health"):
                return _Response(200)
            if url.endswith("/api/tags"):
                return _Response(200, {"models": [{"name": "qwen3:8b"}]})
            raise AssertionError(url)

        def post(self, *args: object, **kwargs: object) -> _Response:
            raise AssertionError("route discovery called a model")

    monkeypatch.setattr(backends.httpx, "Client", lambda timeout: _DiscoveryClient())
    monkeypatch.setattr(backends.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(
        backends,
        "MLXBackend",
        lambda config: pytest.fail("route discovery loaded MLX"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured")

    routes = backends.available_backend_routes(
        Config(backend="auto", model="qwen3:8b", model_source="cli")
    )

    assert routes == ("cc-bridge", "ollama", "mlx", "anthropic")


def test_backend_route_discovery_lists_an_explicit_route_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda *args, **kwargs: pytest.fail("configured route was probed"),
    )

    assert backends.available_backend_routes(Config(backend="cc-bridge")) == (
        "cc-bridge",
    )


@pytest.mark.parametrize(
    "endpoint",
    ["not-an-http-endpoint", "http://127.0.0.1:4141/v1"],
)
def test_backend_route_discovery_does_not_offer_invalid_or_reserved_openai(
    endpoint: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _UnavailableClient:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> _Response:
            raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(backends.httpx, "Client", lambda timeout: _UnavailableClient())
    monkeypatch.setattr(backends.importlib.util, "find_spec", lambda name: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert backends.available_backend_routes(
        Config(backend="auto", base_url=endpoint, model="gpt-test")
    ) == ()


class _FailingTransportClient:
    def __init__(self, error_type: type[httpx.HTTPError], *, healthy_bridge: bool = False) -> None:
        self._error_type = error_type
        self._healthy_bridge = healthy_bridge

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def close(self) -> None:
        return None

    def _failure(self) -> httpx.HTTPError:
        return self._error_type("injected transport failure")

    def get(self, url: str) -> _Response:
        if self._healthy_bridge and url.endswith("/health"):
            return _Response(200)
        raise self._failure()

    def post(self, url: str, **kwargs: object) -> _Response:
        raise self._failure()


def test_explicit_ollama_wraps_read_errors_as_backend_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(httpx.ReadError),
    )

    with pytest.raises(BackendNotFoundError, match="explicitly configured Ollama"):
        resolve_backend(Config(backend="ollama", model="qwen3:8b"))


def test_explicit_cc_bridge_wraps_health_protocol_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(httpx.RemoteProtocolError),
    )

    with pytest.raises(BackendNotFoundError, match="explicitly configured cc-bridge"):
        resolve_backend(Config(backend="cc-bridge", model="route/model"))


def test_explicit_cc_bridge_wraps_canary_read_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(
            httpx.ReadError, healthy_bridge=True
        ),
    )

    with pytest.raises(BackendNotFoundError, match="completion route is unusable"):
        resolve_backend(Config(backend="cc-bridge", model="route/model"))


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_auto_backend_continues_after_local_transport_errors(
    error_type: type[httpx.HTTPError], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(error_type),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    resolved = resolve_backend(Config())

    try:
        assert resolved.identity.backend == "anthropic"
    finally:
        resolved.close()


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_route_discovery_continues_after_local_transport_errors(
    error_type: type[httpx.HTTPError], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(error_type),
    )
    monkeypatch.setattr(backends.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    assert backends.available_backend_routes(Config()) == ("anthropic",)


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.RemoteProtocolError])
def test_auto_backend_continues_after_cc_bridge_canary_transport_errors(
    error_type: type[httpx.HTTPError], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        backends.httpx,
        "Client",
        lambda timeout: _FailingTransportClient(error_type, healthy_bridge=True),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cloud-key")

    resolved = resolve_backend(Config())

    try:
        assert resolved.identity.backend == "anthropic"
    finally:
        resolved.close()
