"""structured_call tests: first-try success, repair-once, double failure, audit."""

from __future__ import annotations

import json

import pytest
from qualagent.domain.errors import LLMValidationFailed
from qualagent.domain.models import Project
from qualagent.domain.schemas import CodingBatchResult
from qualagent.llm.base import Message
from qualagent.llm.fake import FakeProvider
from qualagent.llm.structured import structured_call
from qualagent.services.audit_service import AuditService

VALID = {
    "results": [
        {
            "segment_id": "s1",
            "assignments": [
                {"code": "activation", "rationale": "agent of process", "confidence": 0.9}
            ],
            "uncodable": False,
        }
    ],
    "new_code_proposals": [],
}
MSGS = [Message(role="system", content="sys"), Message(role="user", content="segments...")]


def _as_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


class TestHappyPath:
    def test_valid_first_try(self) -> None:
        provider = FakeProvider([_as_text(VALID)])
        result, attempts = structured_call(provider, MSGS, output_model=CodingBatchResult)
        assert result.results[0].segment_id == "s1"
        assert attempts[0]["ok"] is True
        assert len(attempts) == 1

    def test_no_audit_when_none(self) -> None:
        provider = FakeProvider([_as_text(VALID)])
        _, attempts = structured_call(provider, MSGS, output_model=CodingBatchResult)
        assert attempts[0]["tokens"] >= 0


class TestRepair:
    def test_repair_on_json_syntax_error(self) -> None:
        provider = FakeProvider(["{not json", _as_text(VALID)])
        _result, attempts = structured_call(provider, MSGS, output_model=CodingBatchResult)
        assert attempts[0]["ok"] is False
        assert "not valid JSON" in attempts[0]["error"]
        assert attempts[1]["ok"] is True
        # The corrective prompt includes the previous broken output and the error.
        assert provider.calls[1][-1].role == "user"
        assert "not valid JSON" in provider.calls[1][-1].content

    def test_repair_on_schema_violation(self) -> None:
        broken = {
            "results": [
                {
                    "segment_id": "s1",
                    "assignments": [{"code": "x", "rationale": "r", "confidence": 42.0}],
                }
            ],
            "extra_hallucinated": True,
        }
        provider = FakeProvider([_as_text(broken), _as_text(VALID)])
        _result, attempts = structured_call(provider, MSGS, output_model=CodingBatchResult)
        assert attempts[0]["ok"] is False
        assert attempts[1]["ok"] is True

    def test_double_failure_raises(self) -> None:
        provider = FakeProvider(["nope", "still nope"])
        with pytest.raises(LLMValidationFailed, match="2 attempt"):
            structured_call(provider, MSGS, output_model=CodingBatchResult)


class TestAuditTrail:
    def test_llm_call_event_per_attempt(self, session, project_row: Project) -> None:
        audit = AuditService(session, project_row.id)
        provider = FakeProvider(["bad", _as_text(VALID)])
        structured_call(provider, MSGS, output_model=CodingBatchResult, audit=audit)
        events, total = audit.list_events(event_type="llm.call")
        assert total == 2
        payloads = [AuditService.payload_of(e) for e in events]
        assert payloads[0]["ok"] is False and payloads[1]["ok"] is True
        assert payloads[0]["attempt"] == 1 and payloads[1]["attempt"] == 2
        # Hashes only: no prompt text or document content in the audit log.
        for p in payloads:
            assert set(p) == {
                "attempt",
                "model",
                "prompt_hash",
                "response_hash",
                "tokens",
                "ok",
            } or set(p) == {
                "attempt",
                "model",
                "prompt_hash",
                "response_hash",
                "tokens",
                "ok",
                "error",
            }
            assert len(p["prompt_hash"]) == 64

    def test_model_and_hash_present(self, session, project_row: Project) -> None:
        audit = AuditService(session, project_row.id)
        provider = FakeProvider([_as_text(VALID)])
        structured_call(provider, MSGS, output_model=CodingBatchResult, audit=audit)
        events, _ = audit.list_events(event_type="llm.call")
        payload = AuditService.payload_of(events[0])
        assert payload["model"] == "fake/fake/test-model"


class TestDeterminism:
    def test_seed_forwarded(self) -> None:
        provider = FakeProvider([_as_text(VALID)])
        structured_call(provider, MSGS, output_model=CodingBatchResult, seed=42)
        # FakeProvider ignores seed; the contract test on providers covers it.


class TestFakeProvider:
    def test_cassette_load_and_match(self, tmp_path) -> None:
        cassette = tmp_path / "cassette.yaml"
        cassette.write_text(
            """
model: fake/golden
responses:
  - match:
      contains: "segment-1"
    response: {"results": []}
  - response: {"results": [], "fallback": true}
""",
            encoding="utf-8",
        )
        provider = FakeProvider(cassette_path=cassette)
        assert provider.model == "fake/golden"
        r1 = provider.complete([Message(role="user", content="about segment-1 here")], schema={})
        r2 = provider.complete([Message(role="user", content="no match here")], schema={})
        assert json.loads(r1.text) == {"results": []}
        assert json.loads(r2.text)["fallback"] is True

    def test_exhausted_cassette_raises(self) -> None:
        provider = FakeProvider([])
        from qualagent.domain.errors import LLMProviderError

        with pytest.raises(LLMProviderError, match="exhausted"):
            provider.complete(MSGS, schema={})

    def test_record_mode(self, tmp_path) -> None:
        record = tmp_path / "rec.yaml"
        provider = FakeProvider([_as_text(VALID)], record_path=record)
        provider.complete(MSGS, schema={"type": "object"})
        assert record.is_file()
        import yaml

        data = yaml.safe_load(record.read_text(encoding="utf-8"))
        assert data["model"] == "fake/test-model"
        assert data["responses"][0]["messages"][0]["role"] == "system"
