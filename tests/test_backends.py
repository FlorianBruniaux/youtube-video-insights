from __future__ import annotations

import httpx
import pytest

from yt_insights import backends
from yt_insights.backends import (
    BackendIdentity,
    BackendNotFoundError,
    format_backend_identity,
    resolve_backend,
)
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
