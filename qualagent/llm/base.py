"""LLMProvider protocol and shared LLM types.

Every provider returns :class:`LLMResult` where ``text`` is the raw response
string (expected to be JSON for structured calls) and ``model`` is the exact
model string recorded in CodingRun and audit events (reproducibility, TAD D6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import httpx

from qualagent.domain.errors import LLMProviderError


@dataclass(frozen=True)
class LLMResult:
    """One completed LLM call."""

    text: str
    model: str
    tokens: int = 0
    raw: Any = None


@dataclass(frozen=True)
class Message:
    """A chat message."""

    role: str  # "system" | "user" | "assistant"
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface every provider implements."""

    name: str
    model: str

    @property
    def model_id(self) -> str:
        """Full model identifier, e.g. ``ollama/qwen3:32b``."""
        return f"{self.name}/{self.model}"

    def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResult: ...

    def close(self) -> None: ...


def post_json(
    client: httpx.Client,
    url: str,
    *,
    provider_name: str,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST a JSON body and return the parsed response; map errors to LLMProviderError."""
    try:
        response = client.post(url, json=json_body, headers=headers)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data
    except httpx.HTTPError as exc:
        detail = ""
        if isinstance(exc, httpx.HTTPStatusError):
            detail = f" status={exc.response.status_code} body={exc.response.text[:300]}"
        raise LLMProviderError(f"{provider_name} request failed:{detail} {exc}") from exc
    except ValueError as exc:
        raise LLMProviderError(f"{provider_name} returned non-JSON response") from exc
