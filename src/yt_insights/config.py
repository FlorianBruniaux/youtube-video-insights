"""Configuration management for yt-insights."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_MODEL = "claude-haiku-4-5"
_CONFIG_PATH = Path.home() / ".config" / "yt-insights" / "config.toml"
_INIT_TEMPLATE = Path.home() / ".config" / "yt-insights"


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    anthropic_version: str = "2023-06-01"
    model: str = DEFAULT_MODEL
    max_transcript_chars: int = 10_000
    max_tokens: int = 2048
    timeout: int = 120
    concurrency: int = 0
    transcripts_dir: Path = field(default_factory=lambda: Path("yt_transcripts"))
    insights_dir: Path = field(default_factory=lambda: Path("yt_insights"))

    def with_url(
        self, url: str, *, model: str | None = None, api_key: str | None = None
    ) -> "Config":
        kwargs: dict = {"base_url": url}
        if model is not None:
            kwargs["model"] = model
        if api_key is not None:
            kwargs["api_key"] = api_key
        return replace(self, **kwargs)


def load_config(overrides: dict) -> Config:
    """Merge config from defaults → TOML → env vars → CLI overrides."""
    cfg = Config()

    # Layer 1: TOML file (optional)
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        cfg = _apply_dict(cfg, data)

    # Layer 2: environment variables
    env_map = {
        "YT_INSIGHTS_BASE_URL": "base_url",
        "YT_INSIGHTS_API_KEY": "api_key",
        "YT_INSIGHTS_MODEL": "model",
        "YT_INSIGHTS_ANTHROPIC_VERSION": "anthropic_version",
        "YT_INSIGHTS_MAX_TRANSCRIPT_CHARS": "max_transcript_chars",
        "YT_INSIGHTS_MAX_TOKENS": "max_tokens",
        "YT_INSIGHTS_TIMEOUT": "timeout",
        "YT_INSIGHTS_CONCURRENCY": "concurrency",
        "YT_INSIGHTS_TRANSCRIPTS_DIR": "transcripts_dir",
        "YT_INSIGHTS_INSIGHTS_DIR": "insights_dir",
    }
    env_data: dict = {}
    for env_key, field_name in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            env_data[field_name] = val
    cfg = _apply_dict(cfg, env_data)

    # Layer 3: CLI overrides (None values are ignored)
    clean = {k: v for k, v in overrides.items() if v is not None}
    cfg = _apply_dict(cfg, clean)

    return cfg


def effective_concurrency(config: Config, backend_type: str) -> int:
    if config.concurrency > 0:
        return config.concurrency
    # MLX and Ollama serialize anyway — no benefit beyond 1
    if backend_type in ("mlx", "ollama"):
        return 1
    return 3


def _apply_dict(cfg: Config, data: dict) -> Config:
    """Apply a flat dict of field values to a Config, coercing types."""
    int_fields = {"max_transcript_chars", "max_tokens", "timeout", "concurrency"}
    path_fields = {"transcripts_dir", "insights_dir"}
    updates: dict = {}
    for key, val in data.items():
        if not hasattr(cfg, key):
            continue
        if key in int_fields:
            updates[key] = int(val)
        elif key in path_fields:
            updates[key] = Path(val)
        else:
            updates[key] = val
    return replace(cfg, **updates) if updates else cfg


CONFIG_TOML_TEMPLATE = """\
# yt-insights configuration
# All values are optional — defaults shown.

# base_url = "https://api.anthropic.com/v1"
# api_key = ""          # or set YT_INSIGHTS_API_KEY env var
# anthropic_version = "2023-06-01"
# model = "claude-haiku-4-5"
# max_transcript_chars = 10000
# max_tokens = 2048
# timeout = 120
# concurrency = 0       # 0 = auto (3 for API, 1 for Ollama/MLX)
# transcripts_dir = "yt_transcripts"
# insights_dir = "yt_insights"
"""
