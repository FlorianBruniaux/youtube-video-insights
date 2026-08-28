"""Configuration management for yt-insights."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

from .paths import DataPaths

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_MODEL = "claude-haiku-4-5"
_CONFIG_PATH = Path.home() / ".config" / "yt-insights" / "config.toml"
_INIT_TEMPLATE = Path.home() / ".config" / "yt-insights"


class _DefaultModel(str):
    """Private marker used only when Config.model is omitted."""


class _OmittedLegacyPath(type(Path())):
    """Path-compatible marker for an omitted legacy directory setting."""


_HISTORIC_PATH_DEFAULTS = {
    "transcripts_dir": Path("output/transcripts"),
    "insights_dir": Path("output/insights"),
    "shorts_dir": Path("output/shorts"),
    "shorts_clips_dir": Path("output/clips"),
    "exports_dir": Path("output/exports"),
}
_DATA_PATH_ATTRIBUTES = {
    "transcripts_dir": "transcripts",
    "insights_dir": "insights",
    "shorts_dir": "shorts",
    "shorts_clips_dir": "clips",
    "exports_dir": "exports",
}


def _default_model() -> str:
    return _DefaultModel(DEFAULT_MODEL)


def _omitted_legacy_path(field_name: str) -> Path:
    return _OmittedLegacyPath(_HISTORIC_PATH_DEFAULTS[field_name])


@dataclass
class Config:
    """Effective settings plus the source of model and endpoint intent.

    Any directly supplied model is a firm caller request, including the default
    model value.
    """
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    anthropic_version: str = "2023-06-01"
    model: str = field(default_factory=_default_model)
    model_source: str = "default"
    base_url_source: str = "default"
    _model_was_omitted: bool = field(default=False, repr=False)
    sub_langs: str = "fr,en"
    max_transcript_chars: int = 10_000
    max_tokens: int = 2048
    timeout: int = 120
    concurrency: int = 0
    data_root: Path = field(default_factory=lambda: Path("output"))
    transcripts_dir: Path | None = field(
        default_factory=lambda: _omitted_legacy_path("transcripts_dir")
    )
    insights_dir: Path | None = field(
        default_factory=lambda: _omitted_legacy_path("insights_dir")
    )
    shorts_dir: Path | None = field(
        default_factory=lambda: _omitted_legacy_path("shorts_dir")
    )
    shorts_clips_dir: Path | None = field(
        default_factory=lambda: _omitted_legacy_path("shorts_clips_dir")
    )
    exports_dir: Path | None = field(
        default_factory=lambda: _omitted_legacy_path("exports_dir")
    )

    def __post_init__(self) -> None:
        if isinstance(self.model, _DefaultModel):
            self._model_was_omitted = True
        self.model = str(self.model)
        if self.model_source == "default" and not self._model_was_omitted:
            self.model_source = "direct"
        if self.base_url_source == "default" and self.base_url != DEFAULT_BASE_URL:
            self.base_url_source = "direct"

    def with_url(
        self,
        url: str,
        *,
        model: str | None = None,
        api_key: str | None = None,
        model_source: str | None = None,
        base_url_source: str | None = None,
    ) -> "Config":
        kwargs: dict = {"base_url": url}
        if model is not None:
            kwargs["model"] = model
            kwargs["_model_was_omitted"] = False
            if model_source is None:
                kwargs["model_source"] = "direct"
        if api_key is not None:
            kwargs["api_key"] = api_key
        if model_source is not None:
            kwargs["model_source"] = model_source
        if base_url_source is not None:
            kwargs["base_url_source"] = base_url_source
        return replace(self, **kwargs)

    @property
    def data_paths(self) -> DataPaths:
        """Return paths derived after applying named legacy directory overrides."""
        defaults = DataPaths.from_root(self.data_root)
        resolved: dict[str, Path] = {}
        for field_name, attribute_name in _DATA_PATH_ATTRIBUTES.items():
            value = getattr(self, field_name)
            resolved[attribute_name] = (
                getattr(defaults, attribute_name)
                if value is None or isinstance(value, _OmittedLegacyPath)
                else value
            )
        return replace(
            defaults,
            transcripts=resolved["transcripts"],
            insights=resolved["insights"],
            shorts=resolved["shorts"],
            clips=resolved["clips"],
            exports=resolved["exports"],
        )


def load_config(overrides: dict) -> Config:
    """Merge config from defaults → TOML → env vars → CLI overrides."""
    cfg = Config()

    # Layer 1: TOML file (optional)
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, "rb") as f:
            data = tomllib.load(f)
        cfg = _apply_dict(cfg, data, "toml")

    # Layer 2: environment variables
    env_map = {
        "YT_INSIGHTS_BASE_URL": "base_url",
        "YT_INSIGHTS_API_KEY": "api_key",
        "YT_INSIGHTS_MODEL": "model",
        "YT_INSIGHTS_SUB_LANGS": "sub_langs",
        "YT_INSIGHTS_ANTHROPIC_VERSION": "anthropic_version",
        "YT_INSIGHTS_MAX_TRANSCRIPT_CHARS": "max_transcript_chars",
        "YT_INSIGHTS_MAX_TOKENS": "max_tokens",
        "YT_INSIGHTS_TIMEOUT": "timeout",
        "YT_INSIGHTS_CONCURRENCY": "concurrency",
        "YT_INSIGHTS_DATA_ROOT": "data_root",
        "YT_INSIGHTS_TRANSCRIPTS_DIR": "transcripts_dir",
        "YT_INSIGHTS_INSIGHTS_DIR": "insights_dir",
        "YT_INSIGHTS_SHORTS_DIR": "shorts_dir",
        "YT_INSIGHTS_SHORTS_CLIPS_DIR": "shorts_clips_dir",
        "YT_INSIGHTS_EXPORTS_DIR": "exports_dir",
    }
    env_data: dict = {}
    for env_key, field_name in env_map.items():
        val = os.getenv(env_key)
        if val is not None:
            env_data[field_name] = val
    cfg = _apply_dict(cfg, env_data, "env")

    # Layer 3: CLI overrides (None values are ignored)
    clean = {k: v for k, v in overrides.items() if v is not None}
    cfg = _apply_dict(cfg, clean, "cli")

    return _derive_data_paths(cfg)


def effective_concurrency(config: Config, backend_type: str) -> int:
    if config.concurrency > 0:
        return config.concurrency
    # MLX and Ollama serialize anyway — no benefit beyond 1
    if backend_type in ("mlx", "ollama"):
        return 1
    return 3


def _apply_dict(cfg: Config, data: dict, source: str) -> Config:
    """Apply a flat dict of field values to a Config, coercing types."""
    int_fields = {"max_transcript_chars", "max_tokens", "timeout", "concurrency"}
    path_fields = {
        "data_root",
        "transcripts_dir",
        "insights_dir",
        "shorts_dir",
        "shorts_clips_dir",
        "exports_dir",
    }
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
        if key == "model":
            updates["model_source"] = source
        elif key == "base_url":
            updates["base_url_source"] = source
    return replace(cfg, **updates) if updates else cfg


def _derive_data_paths(cfg: Config) -> Config:
    """Fill defaults after all configuration layers have been merged."""
    paths = cfg.data_paths
    updates: dict[str, Path] = {"data_root": paths.root}
    for field_name, attribute_name in _DATA_PATH_ATTRIBUTES.items():
        resolved = getattr(paths, attribute_name)
        updates[field_name] = (
            _OmittedLegacyPath(resolved)
            if isinstance(getattr(cfg, field_name), _OmittedLegacyPath)
            else resolved
        )
    return replace(cfg, **updates)


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
# data_root = "output"
# transcripts_dir = "output/transcripts"  # Legacy named override
# insights_dir = "output/insights"        # Legacy named override
# shorts_dir = "output/shorts"            # Legacy named override
# shorts_clips_dir = "output/clips"       # Legacy named override
# exports_dir = "output/exports"          # Legacy named override
"""
