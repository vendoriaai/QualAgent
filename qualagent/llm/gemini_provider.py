"""Google Gemini provider: generateContent with response_schema."""

from __future__ import annotations

from typing import Any

import httpx

from qualagent.config import get_api_key
from qualagent.domain.errors import LLMProviderError, ValidationError
from qualagent.llm.base import LLMResult, Message, post_json

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
TIMEOUT = httpx.Timeout(300.0)


class GeminiProvider:
    """Gemini generateContent with JSON mime type + schema."""

    name = "gemini"

    def __init__(self, model: str, *, api_key: str | None = None) -> None:
        self.model = model
        key = api_key if api_key is not None else get_api_key("gemini")
        if not key:
            raise ValidationError(
                "QUALAGENT_GOOGLE_API_KEY is not set (secrets come from env vars only)"
            )
        self._client = httpx.Client(timeout=TIMEOUT)
        self._key = key

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
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "responseMimeType": "application/json",
            "responseSchema": schema,
        }
        if seed is not None:
            generation_config["seed"] = seed
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_parts:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        url = f"{BASE_URL}/models/{self.model}:generateContent"
        data = post_json(
            self._client,
            url,
            provider_name=self.name,
            json_body=body,
            headers={"x-goog-api-key": self._key},
        )
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            usage = data.get("usageMetadata", {})
            tokens = int(usage.get("totalTokenCount", 0))
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(f"Unexpected Gemini response shape: {exc}") from exc
        if not isinstance(text, str):
            raise LLMProviderError("Gemini returned non-text content")
        return LLMResult(text=text, model=self.model_id, tokens=tokens, raw=data)

    def close(self) -> None:
        self._client.close()
