"""OpenAI provider: chat completions with JSON-schema response format."""

from __future__ import annotations

from typing import Any

import httpx

from qualagent.config import get_api_key
from qualagent.domain.errors import LLMProviderError, ValidationError
from qualagent.llm.base import LLMResult, Message, post_json

API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = httpx.Timeout(300.0)


class OpenAIProvider:
    """OpenAI Chat Completions with structured output (response_format)."""

    name = "openai"

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self.model = model
        key = api_key if api_key is not None else get_api_key("openai")
        if not key:
            raise ValidationError(
                "QUALAGENT_OPENAI_API_KEY is not set (secrets come from env vars only)"
            )
        self._client = httpx.Client(timeout=TIMEOUT, headers={"Authorization": f"Bearer {key}"})

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
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "qualagent_output", "schema": schema},
            },
        }
        if seed is not None:
            body["seed"] = seed
        data = post_json(self._client, API_URL, provider_name=self.name, json_body=body)
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            tokens = int(usage.get("total_tokens", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"Unexpected OpenAI response shape: {exc}") from exc
        if not isinstance(text, str):
            raise LLMProviderError("OpenAI returned non-text content")
        return LLMResult(text=text, model=self.model_id, tokens=tokens, raw=data)

    def close(self) -> None:
        self._client.close()
