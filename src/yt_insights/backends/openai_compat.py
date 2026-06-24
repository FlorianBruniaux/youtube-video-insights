"""OpenAI-compatible backend supporting both Anthropic and OpenAI SSE formats.

cc-bridge note: when using cc-bridge (port 4141), the model ID format
"anthropic/{provider}/{model}" (e.g. "anthropic/github_copilot/gpt-5-mini")
routes directly to the named provider using cc-bridge's stored credentials.
A plain model ID (e.g. "claude-haiku-4-5") uses cc-bridge's active_route and
may trigger a 401 if active_route forwards to Anthropic in OAuth passthrough mode.
"""

from __future__ import annotations

import json
from typing import Iterator

import httpx

from .base import BackendUnavailableError, LLMBackend
from ..config import Config


class OpenAICompatBackend:
    """Sync httpx backend for any OpenAI-compatible endpoint.

    Handles two SSE formats in a single stream() code path:

    Anthropic format:
      event: content_block_delta   -> delta.type == "text_delta" -> delta.text
      event: message_delta         -> delta.stop_reason

    OpenAI format:
      data: {"choices": [{"delta": {"content": "..."},
                          "finish_reason": "stop"|"length"|null}]}
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        base = config.base_url.rstrip("/")
        # Normalise: if base_url already ends with /messages (Anthropic direct),
        # use it as-is; otherwise append /chat/completions for OpenAI path or
        # let the caller deal with it. We detect format from the response.
        self._messages_url = f"{base}/messages"
        self._chat_url = f"{base}/chat/completions"
        self._client = httpx.Client(timeout=None)  # per-request timeouts

    # ------------------------------------------------------------------
    # LLMBackend interface
    # ------------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, str]:
        parts: list[str] = []
        stop_reason = "end_turn"
        for chunk in self.stream(prompt, max_tokens=max_tokens, timeout=timeout):
            if isinstance(chunk, tuple):
                # Internal signal: (stop_reason,)
                stop_reason = chunk[0]
            else:
                parts.append(chunk)
        return "".join(parts).strip(), stop_reason

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int,
        timeout: int,
    ) -> Iterator[str]:
        cfg = self._config
        headers: dict = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
            headers["x-api-key"] = cfg.api_key
        if cfg.anthropic_version:
            headers["anthropic-version"] = cfg.anthropic_version
        body = {
            "model": cfg.model,
            "max_tokens": max_tokens,
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Try Anthropic /messages first, fall back to /chat/completions
        url = self._messages_url
        try:
            with self._client.stream(
                "POST", url, headers=headers, json=body, timeout=timeout
            ) as resp:
                if resp.status_code == 404:
                    # Endpoint not found — try OpenAI path
                    url = self._chat_url
                    resp.close()
                else:
                    resp.raise_for_status()
                    yield from self._parse_sse(resp)
                    return
        except httpx.ConnectError as exc:
            raise BackendUnavailableError(
                f"Cannot connect to {url}: {exc}"
            ) from exc

        # OpenAI-compatible path
        try:
            with self._client.stream(
                "POST", url, headers=headers, json=body, timeout=timeout
            ) as resp:
                resp.raise_for_status()
                yield from self._parse_sse(resp)
        except httpx.ConnectError as exc:
            raise BackendUnavailableError(
                f"Cannot connect to {url}: {exc}"
            ) from exc

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # SSE parsing — handles both Anthropic and OpenAI wire formats
    # ------------------------------------------------------------------

    def _parse_sse(self, resp: httpx.Response) -> Iterator[str]:
        stop_reason = "end_turn"

        for raw in resp.iter_lines():
            if not raw:
                continue
            line = raw if isinstance(raw, str) else raw.decode("utf-8")
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload in ("[DONE]", "[done]"):
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue

            # Anthropic format
            if event.get("type") == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield text
            elif event.get("type") == "message_delta":
                reason = event.get("delta", {}).get("stop_reason")
                if reason:
                    stop_reason = reason

            # OpenAI format
            elif "choices" in event:
                choices = event["choices"]
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                text = delta.get("content") or ""
                if text:
                    yield text
                finish = choice.get("finish_reason")
                if finish:
                    # Map OpenAI "length" -> "max_tokens"
                    stop_reason = "max_tokens" if finish == "length" else finish

        # Yield stop_reason as a tagged tuple so generate() can extract it
        # without needing a separate channel
        yield (stop_reason,)  # type: ignore[misc]
