"""Pydantic schema validation tests (LLM envelope strictness especially)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from qualagent.domain.schemas import (
    CodeIn,
    CodingBatchResult,
    DecisionIn,
    LLMAssignment,
    LLMNewCodeProposal,
    LLMSegmentResult,
)


class TestCodingBatchResult:
    def test_valid_envelope(self) -> None:
        data = {
            "results": [
                {
                    "segment_id": "seg-1",
                    "assignments": [
                        {
                            "code": "activation",
                            "rationale": "Actor is agent of 'explained'",
                            "confidence": 0.86,
                        }
                    ],
                    "uncodable": False,
                }
            ],
            "new_code_proposals": [
                {"name": "teacher_agency", "definition": "...", "example_segment_id": "seg-1"}
            ],
        }
        result = CodingBatchResult.model_validate(data)
        assert result.results[0].assignments[0].code == "activation"
        assert result.new_code_proposals[0].name == "teacher_agency"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CodingBatchResult.model_validate({"results": [], "hallucinated_field": True})

    def test_nested_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMSegmentResult.model_validate({"segment_id": "s", "assignments": [], "surprise": 1})
        with pytest.raises(ValidationError):
            LLMAssignment.model_validate(
                {"code": "a", "rationale": "r", "confidence": 0.5, "extra": "x"}
            )

    def test_confidence_bounds(self) -> None:
        with pytest.raises(ValidationError):
            LLMAssignment.model_validate({"code": "a", "rationale": "r", "confidence": 1.5})
        with pytest.raises(ValidationError):
            LLMAssignment.model_validate({"code": "a", "rationale": "r", "confidence": -0.1})

    def test_empty_rationale_rejected(self) -> None:
        with pytest.raises(ValidationError):
            LLMAssignment.model_validate({"code": "a", "rationale": "", "confidence": 0.5})

    def test_code_name_pattern(self) -> None:
        with pytest.raises(ValidationError):
            LLMNewCodeProposal.model_validate({"name": "Not-Snake"})
        CodeIn(name="valid_name")  # ok


class TestDecisionIn:
    def test_actions(self) -> None:
        assert DecisionIn(action="approve").action == "approve"
        with pytest.raises(ValidationError):
            DecisionIn(action="delete")
