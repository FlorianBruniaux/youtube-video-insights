"""MLX local backend for Apple Silicon.

Requires the [mlx] extra: pip install yt-insights[mlx]
MLX generate() is synchronous; stream() yields the full result in one chunk
since mlx-lm has no streaming API.
"""

from __future__ import annotations

from typing import Iterator

from ..config import Config


class MLXBackend:
    def __init__(self, config: Config) -> None:
        try:
            import mlx_lm  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "mlx-lm is not installed. Run: pip install yt-insights[mlx]"
            ) from exc
        self._config = config
        self._mlx_lm = __import__("mlx_lm")

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, str]:
        text = self._mlx_lm.generate(
            prompt,
            max_tokens=max_tokens,
            verbose=False,
        )
        # MLX doesn't expose stop_reason; assume normal completion
        return text.strip(), "end_turn"

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> Iterator[str]:
        text, _ = self.generate(prompt, max_tokens=max_tokens, timeout=timeout)
        yield text

    def close(self) -> None:
        pass
