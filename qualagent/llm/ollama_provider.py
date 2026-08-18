"""Ollama provider: local /api/chat with JSON format and seeded options."""

from __future__ import annotations

from typing import Any

import httpx

from qualagent.config import get_ollama_host
from qualagent.domain.errors import LLMProviderError
from qualagent.llm.base import LLMResult, Message, post_json

TIMEOUT = httpx.Timeout(600.0)  # local models can be slow on first load


class OllamaProvider:
    """Local Ollama chat API (privacy-first default provider)."""

    name = "ollama"

    def __init__(self, model: str, *, host: str | None = None) -> None:
        self.model = model
        self._host = (host if host is not None else get_ollama_host()).rstrip("/")
        self._client = httpx.Client(timeout=TIMEOUT)

    @property
    def model_id(self) -> str:
        return f"{self.name}/{self.model}"

    def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResult:
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": False,
            "format": schema if schema else "json",
            "options": options,
        }
        data = post_json(
            self._client, f"{self._host}/api/chat", provider_name=self.name, json_body=body
        )
        try:
            text = data["message"]["content"]
            tokens = int(data.get("prompt_eval_count", 0)) + int(data.get("eval_count", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"Unexpected Ollama response shape: {exc}") from exc
        if not isinstance(text, str):
            raise LLMProviderError("Ollama returned non-text content")
        return LLMResult(text=text, model=self.model_id, tokens=tokens, raw=data)

    def close(self) -> None:
        self._client.close()
