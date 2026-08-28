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
            import mlx_lm
        except ImportError as exc:
            raise ImportError(
                "mlx-lm is not installed. Run: pip install yt-insights[mlx]"
            ) from exc
        self._config = config
        self._mlx_lm = mlx_lm
        self._model = None
        self._tokenizer = None

    def _ensure_loaded(self) -> tuple[object, object]:
        if self._model is None or self._tokenizer is None:
            self._model, self._tokenizer = self._mlx_lm.load(self._config.model)
        return self._model, self._tokenizer

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, str]:
        model, tokenizer = self._ensure_loaded()
        text = self._mlx_lm.generate(
            model,
            tokenizer,
            prompt=prompt,
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
