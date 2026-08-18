"""Anthropic provider: messages API with forced tool-use JSON output."""

from __future__ import annotations

import json
from typing import Any

import httpx

from qualagent.config import get_api_key
from qualagent.domain.errors import LLMProviderError, ValidationError
from qualagent.llm.base import LLMResult, Message, post_json

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
TIMEOUT = httpx.Timeout(300.0)
TOOL_NAME = "emit_json"


class AnthropicProvider:
    """Anthropic Messages API; structured output via forced tool use."""

    name = "anthropic"

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self.model = model
        key = api_key if api_key is not None else get_api_key("anthropic")
        if not key:
            raise ValidationError(
                "QUALAGENT_ANTHROPIC_API_KEY is not set (secrets come from env vars only)"
            )
        self._client = httpx.Client(
            timeout=TIMEOUT,
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
        )

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
        system_parts = [m.content for m in messages if m.role == "system"]
        chat = [m.as_dict() for m in messages if m.role in ("user", "assistant")]
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": temperature,
            "system": "\n\n".join(system_parts) if system_parts else None,
            "messages": chat,
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Emit the structured coding result as JSON.",
                    "input_schema": schema,
                }
            ],
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
        }
        body = {k: v for k, v in body.items() if v is not None}
        # Anthropic has no seed parameter; determinism relies on temperature 0.
        _ = seed
        data = post_json(self._client, API_URL, provider_name=self.name, json_body=body)
        try:
            tool_block = next(block for block in data["content"] if block.get("type") == "tool_use")
            payload = tool_block["input"]
            usage = data.get("usage", {})
            tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
        except (KeyError, StopIteration, TypeError, ValueError) as exc:
            raise LLMProviderError(f"Unexpected Anthropic response shape: {exc}") from exc
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            model=self.model_id,
            tokens=tokens,
            raw=data,
        )

    def close(self) -> None:
        self._client.close()
