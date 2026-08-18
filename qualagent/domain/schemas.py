"""Pydantic I/O schemas (API + LLM-facing) per 04_DATA_MODEL section 2.

LLM-facing schemas use ``extra="forbid"`` so hallucinated fields fail
validation immediately (builder rule 10).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# API-facing schemas
# ---------------------------------------------------------------------------


class ProjectIn(BaseModel):
    """Request body for creating a project."""

    name: str = Field(min_length=1, max_length=200)
    pack: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    created_at: datetime
    active_pack: str | None = None
    active_codebook_version: int | None = None


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    filename: str
    sha256: str
    mime: str
    imported_at: datetime
    segment_count: int = 0


class SegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    index: int
    start_offset: int
    end_offset: int
    speaker: str | None = None
    segmenter_version: str
    text: str


class CodeIn(BaseModel):
    """Request body for creating a code manually."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    definition: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    parent_id: str | None = None
    example_segment_ids: list[str] = Field(default_factory=list)


class CodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    codebook_id: str
    parent_id: str | None = None
    name: str
    definition: str
    inclusion_criteria: str
    exclusion_criteria: str
    example_segment_ids: list[str] = Field(default_factory=list)


class CodebookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    version: int
    status: str
    locked_at: datetime | None = None
    codes: list[CodeOut] = Field(default_factory=list)


class DecisionIn(BaseModel):
    """Human review decision on an assignment."""

    action: Literal["approve", "reject", "edit"]
    code_id: str | None = None  # required for edit
    note: str | None = None


class AssignmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    segment_id: str
    code_id: str
    run_id: str | None = None
    source: str
    rationale: str
    confidence: float | None = None
    status: str
    created_at: datetime


class RunIn(BaseModel):
    """Request body for starting a coding run."""

    pack: str
    codebook_version: int | None = None
    batch_size: int = Field(default=20, ge=1, le=200)


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    pack: str
    codebook_version: int
    model: str
    temperature: float
    seed: int | None = None
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    stats: dict[str, Any] = Field(default_factory=dict)


class IRRReport(BaseModel):
    """Cohen's kappa agreement between AI and human-verified coding."""

    overall_kappa: float
    per_code: dict[str, float] = Field(default_factory=dict)
    n_compared: int = 0


class Page(BaseModel):
    """REST pagination envelope."""

    items: list[Any]
    total: int


# ---------------------------------------------------------------------------
# LLM-facing schemas (extra="forbid" everywhere)
# ---------------------------------------------------------------------------


class LLMAssignment(BaseModel):
    """One code assignment inside the LLM output envelope."""

    model_config = ConfigDict(extra="forbid")

    code: str
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    actor_text: str | None = None  # exact span being coded (van Leeuwen pack)


class LLMSegmentResult(BaseModel):
    """Coding result for one segment."""

    model_config = ConfigDict(extra="forbid")

    segment_id: str
    assignments: list[LLMAssignment] = Field(default_factory=list)
    uncodable: bool = False


class LLMNewCodeProposal(BaseModel):
    """Draft code proposal; never auto-applied to the codebook."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    definition: str = ""
    example_segment_id: str | None = None


class CodingBatchResult(BaseModel):
    """The structured-output envelope every coding LLM call must return."""

    model_config = ConfigDict(extra="forbid")

    results: list[LLMSegmentResult]
    new_code_proposals: list[LLMNewCodeProposal] = Field(default_factory=list)


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    ts: datetime
    actor: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
