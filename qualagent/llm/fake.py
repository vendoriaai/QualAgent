"""Fake LLM provider: scripted YAML cassettes for offline tests (TAD section 3.4).

Cassette format::

    model: fake/test
    responses:
      - response: {"results": []}          # dict -> serialized as JSON text
      - response: "raw text"               # plain string used verbatim
      - match:
          contains: "activation"           # only consumed when the prompt
        response: {...}                    # contains this substring

Responses are consumed in order; a ``match`` entry is used when an earlier
unused entry matches the prompt. Record mode appends real calls to a cassette
file for golden tests (ADR-003).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from qualagent.domain.errors import LLMProviderError
from qualagent.llm.base import LLMResult, Message


@dataclass
class _ScriptedResponse:
    response: str
    contains: str | None = None
    used: bool = field(default=False)


class FakeProvider:
    """Deterministic in-process provider for tests and offline demos."""

    name = "fake"

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        cassette_path: Path | None = None,
        record_path: Path | None = None,
        model: str = "fake/test-model",
    ) -> None:
        self.model = model
        self.calls: list[list[Message]] = []
        self._record_path = record_path
        self._recorded: list[dict[str, Any]] = []
        self._script: list[_ScriptedResponse] = [
            _ScriptedResponse(response=r) for r in (responses or [])
        ]
        if cassette_path is not None:
            self._load_cassette(cassette_path)

    @property
    def model_id(self) -> str:
        return f"{self.name}/{self.model}"

    def _load_cassette(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.model = str(data.get("model", self.model))
        self._script = []
        for entry in data.get("responses", []):
            contains = None
            if isinstance(entry, dict) and "match" in entry:
                contains = entry["match"].get("contains")
            raw = entry["response"] if isinstance(entry, dict) else entry
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            self._script.append(_ScriptedResponse(response=raw, contains=contains))

    def complete(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        temperature: float = 0.0,
        seed: int | None = None,
    ) -> LLMResult:
        self.calls.append(list(messages))
        prompt_text = "\n".join(m.content for m in messages)
        scripted = self._next_response(prompt_text)
        if scripted is None:
            raise LLMProviderError("FakeProvider cassette exhausted")
        if self._record_path is not None:
            self._recorded.append(
                {
                    "messages": [m.as_dict() for m in messages],
                    "response": scripted,
                    "schema": schema,
                }
            )
            self._flush_record()
        return LLMResult(text=scripted, model=self.model_id, tokens=len(scripted) // 4)

    def _next_response(self, prompt_text: str) -> str | None:
        # Prefer the first unused matching entry; otherwise the next in order.
        for entry in self._script:
            if entry.contains is not None and not entry.used and entry.contains in prompt_text:
                entry.used = True
                return entry.response
        for entry in self._script:
            if not entry.used and entry.contains is None:
                entry.used = True
                return entry.response
        return None

    def _flush_record(self) -> None:
        if self._record_path is None:
            return
        self._record_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model": self.model, "responses": self._recorded}
        self._record_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )

    def close(self) -> None:
        return None
