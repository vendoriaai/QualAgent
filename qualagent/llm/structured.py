"""Structured LLM calls: schema-constrained output with one corrective retry.

Flow (TAD D5): call provider -> parse JSON -> validate against the Pydantic
envelope -> on failure, append the validation error and retry once -> second
failure raises :class:`LLMValidationFailed` (the caller flags the segments
``needs_human``).

Every attempt emits an ``llm.call`` audit event. Payloads contain hashes only
(prompt_hash, response_hash) so document text never lands in the audit log even
for local providers (TAD section 8); the raw prompt/response stay reproducible
via the recorded model + seed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from qualagent.domain.errors import LLMValidationFailed
from qualagent.llm.base import LLMProvider, LLMResult, Message
from qualagent.services.audit_service import AuditService

T = TypeVar("T", bound=BaseModel)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _first_error(exc: PydanticValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "unknown validation error"
    first = errors[0]
    loc = ".".join(str(part) for part in first.get("loc", ()))
    return f"{loc or 'root'}: {first.get('msg', 'invalid')}"


def structured_call(  # noqa: UP047 - TypeVar keeps 3.12-compatible signature
    provider: LLMProvider,
    messages: list[Message],
    *,
    output_model: type[T],
    temperature: float = 0.0,
    seed: int | None = None,
    audit: AuditService | None = None,
    max_corrective_retries: int = 1,
    json_schema_hint: dict[str, Any] | None = None,
) -> tuple[T, list[dict[str, Any]]]:
    """Call the provider and return the validated output model.

    Args:
        provider: Configured LLM provider.
        messages: Prompt messages (system + user).
        output_model: Pydantic envelope the response must satisfy.
        temperature: Sampling temperature (default 0).
        seed: Determinism seed where the provider supports it.
        audit: Audit service; an ``llm.call`` event is emitted per attempt.
        max_corrective_retries: Corrective retries after the first failure
            (D5 default: 1; second failure raises).
        json_schema_hint: JSON Schema passed to the provider for
            provider-native structured output (derived from output_model when
            omitted).

    Returns:
        (validated_model, attempts) where attempts describe each try.

    Raises:
        LLMValidationFailed: When output is still invalid after retries.
    """
    schema = json_schema_hint or output_model.model_json_schema()
    attempt_messages = list(messages)
    attempts: list[dict[str, Any]] = []
    last_error = "unknown error"

    for attempt_no in range(1 + max(0, max_corrective_retries)):
        result: LLMResult = provider.complete(
            attempt_messages,
            schema=schema,
            temperature=temperature,
            seed=seed,
        )
        prompt_text = json.dumps([m.as_dict() for m in attempt_messages], ensure_ascii=False)
        record = {
            "attempt": attempt_no + 1,
            "model": result.model,
            "prompt_hash": _hash_text(prompt_text),
            "response_hash": _hash_text(result.text),
            "tokens": result.tokens,
            "ok": False,
        }
        try:
            parsed = json.loads(result.text)
        except json.JSONDecodeError as exc:
            last_error = f"response is not valid JSON: {exc.msg}"
            record["error"] = last_error
            attempts.append(record)
            if audit is not None:
                audit.emit("ai", "llm.call", record)
            attempt_messages = [
                *attempt_messages,
                Message(role="assistant", content=result.text[:4000]),
                Message(
                    role="user",
                    content=(
                        f"Your previous response was invalid: {last_error}. "
                        "Respond again with ONLY a JSON object matching the schema."
                    ),
                ),
            ]
            continue
        try:
            validated = output_model.model_validate(parsed)
        except PydanticValidationError as exc:
            last_error = _first_error(exc)
            record["error"] = last_error
            attempts.append(record)
            if audit is not None:
                audit.emit("ai", "llm.call", record)
            attempt_messages = [
                *attempt_messages,
                Message(role="assistant", content=result.text[:4000]),
                Message(
                    role="user",
                    content=(
                        f"Your previous response failed validation: {last_error}. "
                        "Respond again with ONLY a JSON object matching the schema, "
                        "including every required field and no extra fields."
                    ),
                ),
            ]
            continue
        record["ok"] = True
        attempts.append(record)
        if audit is not None:
            audit.emit("ai", "llm.call", record)
        return validated, attempts

    raise LLMValidationFailed(
        f"Structured output invalid after {len(attempts)} attempt(s): {last_error}"
    )
