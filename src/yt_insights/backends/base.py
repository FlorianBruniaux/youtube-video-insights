"""LLM backend Protocol and exception types."""

from __future__ import annotations

from typing import Iterator, Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """Synchronous LLM backend interface.

    generate() returns (text, stop_reason). stop_reason is one of:
      "end_turn"   - normal completion
      "max_tokens" - output was truncated; callers must NOT cache this
      "stop"       - stopped at a stop sequence
    """

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, str]: ...

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> Iterator[str]: ...

    def close(self) -> None: ...


class BackendNotFoundError(Exception):
    """No usable LLM backend could be auto-detected."""


class BackendUnavailableError(Exception):
    """A backend was selected but failed to respond."""
