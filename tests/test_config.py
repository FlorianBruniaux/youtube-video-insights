from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from yt_insights import config as config_module
from yt_insights.config import DEFAULT_MODEL, Config, effective_concurrency, load_config

OLLAMA_URL = "http://127.0.0.1:11434/v1"


def test_direct_default_model_has_explicit_source_but_omitted_model_does_not() -> None:
    """Detects collapsing an explicit default-model request into auto-detection."""
    assert Config().model_source == "default"
    assert Config(model=DEFAULT_MODEL).model_source == "direct"


def test_direct_config_keeps_the_historic_output_directory_defaults() -> None:
    config = Config()

    assert config.transcripts_dir == Path("output/transcripts")
    assert config.insights_dir == Path("output/insights")
    assert config.shorts_dir == Path("output/shorts")
    assert config.shorts_clips_dir == Path("output/clips")


def test_direct_config_derives_data_paths_from_a_custom_data_root(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom-corpus"

    paths = Config(data_root=custom_root).data_paths

    assert paths.transcripts == custom_root / "transcripts"
    assert paths.insights == custom_root / "insights"
    assert paths.shorts == custom_root / "shorts"
    assert paths.clips == custom_root / "clips"


def test_research_output_root_is_optional_and_is_never_derived_from_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    unrelated_directory = tmp_path / "unrelated"
    unrelated_directory.mkdir()
    monkeypatch.chdir(unrelated_directory)

    assert load_config({"data_root": tmp_path / "corpus"}).research_output_root is None


def test_research_output_root_accepts_cli_and_environment_values(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    configured_root = tmp_path / "tracked"

    config = load_config(
        {"data_root": tmp_path / "corpus", "research_output_root": configured_root}
    )

    assert config.research_output_root == configured_root

    environment_root = tmp_path / "from-environment"
    monkeypatch.setenv("YT_INSIGHTS_RESEARCH_OUTPUT_ROOT", str(environment_root))
    assert load_config({}).research_output_root == environment_root


def test_config_template_documents_research_output_root_without_secrets() -> None:
    assert "research_output_root" in config_module.CONFIG_TOML_TEMPLATE
    assert str(Path.home()) not in config_module.CONFIG_TOML_TEMPLATE


def test_direct_config_honors_an_explicit_legacy_directory_override(tmp_path: Path) -> None:
    custom_root = tmp_path / "custom-corpus"
    transcript_override = Path("output/transcripts")

    paths = Config(
        data_root=custom_root, transcripts_dir=transcript_override
    ).data_paths

    assert paths.transcripts == transcript_override
    assert paths.insights == custom_root / "insights"


def test_replace_config_honors_a_new_explicit_legacy_directory_override(
    tmp_path: Path,
) -> None:
    custom_root = tmp_path / "custom-corpus"
    transcript_override = tmp_path / "replaced-transcripts"

    paths = replace(
        Config(data_root=custom_root), transcripts_dir=transcript_override
    ).data_paths

    assert paths.transcripts == transcript_override
    assert paths.insights == custom_root / "insights"


def test_replace_config_treats_the_historic_path_as_an_explicit_override(
    tmp_path: Path,
) -> None:
    custom_root = tmp_path / "custom-corpus"
    transcript_override = Path("output/transcripts")

    paths = replace(
        Config(data_root=custom_root), transcripts_dir=transcript_override
    ).data_paths

    assert paths.transcripts == transcript_override
    assert paths.insights == custom_root / "insights"


def test_mutating_config_honors_a_new_explicit_legacy_directory_override(
    tmp_path: Path,
) -> None:
    custom_root = tmp_path / "custom-corpus"
    transcript_override = tmp_path / "assigned-transcripts"
    config = Config(data_root=custom_root)

    config.transcripts_dir = transcript_override

    assert config.data_paths.transcripts == transcript_override
    assert config.data_paths.insights == custom_root / "insights"


def test_with_url_marks_a_supplied_default_model_as_direct() -> None:
    """Detects retaining omission state when with_url receives an explicit model."""
    result = Config().with_url(OLLAMA_URL, model=DEFAULT_MODEL)

    assert result.model == DEFAULT_MODEL
    assert result.model_source == "direct"


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
    assert result.model_source == "cli"
    assert result.base_url_source == "env"


def test_backend_precedence_is_cli_then_environment_then_toml(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('backend = "anthropic"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)

    assert load_config({}).backend == "anthropic"

    monkeypatch.setenv("YT_INSIGHTS_BACKEND", "ollama")
    assert load_config({}).backend == "ollama"
    assert load_config({"backend": "mlx"}).backend == "mlx"


@pytest.mark.parametrize("backend", ["", "local", "claude", "OLLAMA", "api"])
def test_config_rejects_unknown_backend_names(backend: str) -> None:
    with pytest.raises(ValueError, match="backend"):
        Config(backend=backend)


def test_config_accepts_every_documented_backend_name() -> None:
    assert config_module.BACKEND_NAMES == (
        "auto",
        "ollama",
        "mlx",
        "cc-bridge",
        "anthropic",
        "openai",
    )
    assert [Config(backend=name).backend for name in config_module.BACKEND_NAMES] == list(
        config_module.BACKEND_NAMES
    )


def test_data_root_precedence_is_cli_then_environment_then_toml_then_output(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    toml_root = tmp_path / "toml-root"
    environment_root = tmp_path / "environment-root"
    cli_root = tmp_path / "cli-root"
    config_path.write_text(f'data_root = "{toml_root}"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)

    assert load_config({}).data_paths.root == toml_root

    monkeypatch.setenv("YT_INSIGHTS_DATA_ROOT", str(environment_root))
    assert load_config({}).data_paths.root == environment_root

    assert load_config({"data_root": cli_root}).data_paths.root == cli_root

    monkeypatch.delenv("YT_INSIGHTS_DATA_ROOT")
    config_path.unlink()
    assert load_config({}).data_paths.root == (Path.cwd() / "output").resolve()


def test_replace_loaded_config_rederives_omitted_paths_from_a_new_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(config_module, "_CONFIG_PATH", tmp_path / "missing.toml")
    first_root = tmp_path / "first-corpus"
    second_root = tmp_path / "second-corpus"

    loaded = load_config({"data_root": first_root})
    moved = replace(loaded, data_root=second_root)

    assert loaded.transcripts_dir == first_root / "transcripts"
    assert moved.data_paths.transcripts == second_root / "transcripts"


def test_legacy_transcript_override_only_replaces_the_transcript_path(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "config.toml"
    data_root = tmp_path / "corpus"
    transcript_override = tmp_path / "legacy-transcripts"
    config_path.write_text(f'data_root = "{data_root}"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "_CONFIG_PATH", config_path)
    monkeypatch.setenv("YT_INSIGHTS_TRANSCRIPTS_DIR", str(transcript_override))

    paths = load_config({}).data_paths

    assert paths.transcripts == transcript_override
    assert paths.insights == data_root / "insights"
    assert paths.search_database == data_root / ".search" / "search-v1.sqlite3"


def test_effective_concurrency_keeps_local_backends_serial() -> None:
    """Detects a regression that concurrently drives local MLX or Ollama backends."""
    assert effective_concurrency(Config(), "mlx") == 1
    assert effective_concurrency(Config(), "ollama") == 1
    assert effective_concurrency(Config(), "api") == 3
    assert effective_concurrency(Config(concurrency=7), "mlx") == 1
    assert effective_concurrency(Config(concurrency=7), "ollama") == 1
    assert effective_concurrency(Config(concurrency=7), "anthropic") == 7
