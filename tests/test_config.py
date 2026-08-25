from __future__ import annotations

from pathlib import Path

from yt_insights import config as config_module
from yt_insights.config import Config, effective_concurrency, load_config


def test_load_config_applies_toml_env_and_cli_in_precedence_order(
    tmp_path: Path, monkeypatch
) -> None:
    """Detects a regression that lets a lower-priority configuration source win."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '\n'.join(
            [
                'base_url = "http://toml.local/v1"',
                'model = "toml-model"',
                'max_tokens = 128',
                'transcripts_dir = "toml-transcripts"',
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)
    monkeypatch.setenv("YT_INSIGHTS_BASE_URL", "http://env.local/v1")
    monkeypatch.setenv("YT_INSIGHTS_MODEL", "env-model")
    monkeypatch.setenv("YT_INSIGHTS_MAX_TOKENS", "256")
    monkeypatch.setenv("YT_INSIGHTS_INSIGHTS_DIR", "env-insights")

    result = load_config(
        {
            "model": "cli-model",
            "max_tokens": 512,
            "transcripts_dir": Path("cli-transcripts"),
            "unused": None,
        }
    )

    assert result.base_url == "http://env.local/v1"
    assert result.model == "cli-model"
    assert result.max_tokens == 512
    assert result.transcripts_dir == Path("cli-transcripts")
    assert result.insights_dir == Path("env-insights")


def test_effective_concurrency_keeps_local_backends_serial() -> None:
    """Detects a regression that concurrently drives local MLX or Ollama backends."""
    assert effective_concurrency(Config(), "mlx") == 1
    assert effective_concurrency(Config(), "ollama") == 1
    assert effective_concurrency(Config(), "api") == 3
    assert effective_concurrency(Config(concurrency=7), "mlx") == 7
