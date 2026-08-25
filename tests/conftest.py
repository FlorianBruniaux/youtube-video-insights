from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class FakeBackend:
    """Deterministic in-memory implementation of the LLMBackend protocol."""

    def __init__(self, responses: list[tuple[str, str]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, int, int]] = []
        self.closed = False

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, str]:
        self.calls.append((prompt, max_tokens, timeout))
        if not self.responses:
            raise AssertionError("The fake backend received an unexpected generate() call.")
        return self.responses.pop(0)

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> Iterator[str]:
        raise AssertionError("The fake backend received an unexpected stream() call.")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sample_fr_vtt() -> Path:
    return FIXTURES_DIR / "sample.fr.vtt"


@pytest.fixture
def sample_en_vtt() -> Path:
    return FIXTURES_DIR / "sample.en.vtt"


@pytest.fixture
def transcript_path(tmp_path: Path, sample_fr_vtt: Path) -> Path:
    path = tmp_path / "20260223 - Build reliable agents [nfupYzLjFGc].fr.vtt"
    path.write_text(sample_fr_vtt.read_text(encoding="utf-8"), encoding="utf-8")
    return path
