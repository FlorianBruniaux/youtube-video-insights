from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


VERIFY_SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_search_slice.py"


def _load_verifier():
    spec = importlib.util.spec_from_file_location("verify_search_slice", VERIFY_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_prepare_artifact_dir_refuses_existing_or_corpus_descendant(tmp_path: Path) -> None:
    verifier = _load_verifier()
    corpus = tmp_path / "output"
    corpus.mkdir()

    with pytest.raises(ValueError, match="inside corpus"):
        verifier.prepare_artifact_dir(corpus / "derived", corpus)

    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="must not already exist"):
        verifier.prepare_artifact_dir(existing, corpus)

    target = tmp_path / "new-run"
    verifier.prepare_artifact_dir(target, corpus)
    assert target.is_dir()


def test_all_gates_requires_exactly_the_declared_critical_gate_names() -> None:
    verifier = _load_verifier()
    passing = {name: True for name in verifier.CRITICAL_GATES}

    assert verifier.all_gates_pass(passing)
    assert not verifier.all_gates_pass({key: value for key, value in passing.items() if key != "tests"})
    assert not verifier.all_gates_pass({**passing, "unknown_gate": True})
    for failed_gate in passing:
        injected = {**passing, failed_gate: False}
        assert not verifier.all_gates_pass(injected), failed_gate


def test_head_identity_requires_expected_prefix_to_resolve_to_observed_head() -> None:
    verifier = _load_verifier()
    observed = "a" * 40
    matching = verifier.CommandResult(
        command=("git", "rev-parse", "HEAD"),
        cwd="/worktree",
        exit_code=0,
        stdout=observed + "\n",
        stderr="",
        duration_ms=1.0,
    )
    resolved_expected = verifier.CommandResult(
        command=("git", "rev-parse", "--verify", "aaaa^{commit}"),
        cwd="/worktree",
        exit_code=0,
        stdout=observed + "\n",
        stderr="",
        duration_ms=1.0,
    )
    mismatching = verifier.CommandResult(
        command=("git", "rev-parse", "--verify", "bbbb^{commit}"),
        cwd="/worktree",
        exit_code=0,
        stdout="b" * 40 + "\n",
        stderr="",
        duration_ms=1.0,
    )

    assert verifier.head_identity_matches("aaaa", matching, resolved_expected) == (True, observed, observed)
    assert verifier.head_identity_matches("bbbb", matching, mismatching) == (False, observed, "b" * 40)


def test_validate_hit_rejects_url_video_id_not_matching_source_filename(tmp_path: Path) -> None:
    verifier = _load_verifier()
    corpus = tmp_path / "output"
    source = corpus / "channel" / "transcripts" / "Talk [VideoId_123].en.vtt"
    source.parent.mkdir(parents=True)
    source.write_text("WEBVTT\n", encoding="utf-8")

    errors = verifier.validate_hit(
        {
            "source": "channel/transcripts/Talk [VideoId_123].en.vtt",
            "timestamp": "00:00:00",
            "url": "https://youtube.com/watch?v=AAAAAAAAAAA&t=0s",
        },
        corpus,
    )

    assert "source_video_id_mismatch" in errors


def test_validate_hit_rejects_noncanonical_url_with_extra_query_parameter(tmp_path: Path) -> None:
    verifier = _load_verifier()
    corpus = tmp_path / "output"
    source = corpus / "channel" / "transcripts" / "Talk [VideoId_123].en.vtt"
    source.parent.mkdir(parents=True)
    source.write_text("WEBVTT\n", encoding="utf-8")

    errors = verifier.validate_hit(
        {
            "source": "channel/transcripts/Talk [VideoId_123].en.vtt",
            "timestamp": "00:00:00",
            "url": "https://youtube.com/watch?v=VideoId_123&t=0s&extra=1",
        },
        corpus,
    )

    assert "url_not_canonical" in errors


def test_invoke_cli_uses_the_explicit_worktree_source_without_subprocess(
    tmp_path: Path, monkeypatch
) -> None:
    verifier = _load_verifier()
    worktree = tmp_path / "worktree"
    cli_path = worktree / "src" / "yt_insights" / "cli.py"
    cli_path.parent.mkdir(parents=True)
    (cli_path.parent / "__init__.py").write_text("", encoding="utf-8")
    cli_path.write_text(
        "import click\n"
        "@click.command()\n"
        "@click.argument('value')\n"
        "def cli(value):\n"
        "    click.echo(f'fake:{value}')\n",
        encoding="utf-8",
    )

    def should_not_spawn(*args, **kwargs):
        raise AssertionError("CLI verifier must use the explicit worktree in-process")

    monkeypatch.setattr(verifier, "run_command", should_not_spawn)
    for name in tuple(sys.modules):
        if name == "yt_insights" or name.startswith("yt_insights."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    result = verifier.invoke_cli(worktree, ["value"])

    assert result.exit_code == 0
    assert result.stdout == "fake:value\n"
