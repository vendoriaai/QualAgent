"""SQLModel entities per 04_DATA_MODEL section 1.

Conventions:
- IDs are UUIDv4 strings.
- Datetimes are stored as naive UTC in SQLite and normalized back to
  timezone-aware UTC via :func:`as_utc` at service boundaries.
- ``Segment.text`` is always derived: ``document.raw_text[start_offset:end_offset]``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


def new_id() -> str:
    """Return a fresh UUIDv4 string."""
    return str(uuid4())


def utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime (SQLite storage format)."""
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


class Project(SQLModel, table=True):
    """A study: documents + codebook + coding state + audit log."""

    __tablename__ = "project"

    id: str = Field(primary_key=True, default_factory=new_id)
    name: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow_naive)
    config_json: str = Field(default="{}")
    active_pack: str | None = Field(default=None)  # e.g. "van_leeuwen@1.0.0"
    active_codebook_version: int | None = Field(default=None)


class Document(SQLModel, table=True):
    """An ingested source file, normalized to plain text."""

    __tablename__ = "document"

    id: str = Field(primary_key=True, default_factory=new_id)
    project_id: str = Field(foreign_key="project.id", index=True)
    filename: str
    sha256: str  # provenance hash of the original file bytes
    mime: str
    raw_text: str  # normalized plain text (Unicode NFC)
    imported_at: datetime = Field(default_factory=utcnow_naive)


class Segment(SQLModel, table=True):
    """A codable unit with immutable character offsets into Document.raw_text."""

    __tablename__ = "segment"
    __table_args__ = (UniqueConstraint("document_id", "index", name="uq_segment_doc_index"),)

    id: str = Field(primary_key=True, default_factory=new_id)
    document_id: str = Field(foreign_key="document.id", index=True)
    index: int  # order within the document
    start_offset: int
    end_offset: int
    speaker: str | None = None  # interview turn speaker, when detected
    segmenter_version: str  # e.g. "turns@1"


class Codebook(SQLModel, table=True):
    """A versioned codebook (draft -> refined -> locked)."""

    __tablename__ = "codebook"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_codebook_project_version"),
    )

    id: str = Field(primary_key=True, default_factory=new_id)
    project_id: str = Field(foreign_key="project.id", index=True)
    version: int  # per-project monotonic
    status: str = Field(default="draft")  # draft | refined | locked
    locked_at: datetime | None = None


class Code(SQLModel, table=True):
    """A code definition inside a codebook; supports one-level-plus taxonomies."""

    __tablename__ = "code"
    __table_args__ = (UniqueConstraint("codebook_id", "name", name="uq_code_codebook_name"),)

    id: str = Field(primary_key=True, default_factory=new_id)
    codebook_id: str = Field(foreign_key="codebook.id", index=True)
    parent_id: str | None = Field(default=None, foreign_key="code.id")
    name: str  # snake_case, unique per codebook
    definition: str = Field(default="")
    inclusion_criteria: str = Field(default="")
    exclusion_criteria: str = Field(default="")
    example_segment_ids: str = Field(default="[]")  # JSON array of segment ids


class CodingRun(SQLModel, table=True):
    """One execution of the coding pipeline; snapshots pack + codebook version."""

    __tablename__ = "codingrun"

    id: str = Field(primary_key=True, default_factory=new_id)
    project_id: str = Field(foreign_key="project.id", index=True)
    pack: str  # "name@version"
    codebook_version: int
    model: str  # exact model string, e.g. "ollama/qwen3:32b"
    temperature: float = 0.0
    seed: int | None = None
    status: str = Field(default="running")  # running | completed | failed
    started_at: datetime = Field(default_factory=utcnow_naive)
    finished_at: datetime | None = None
    stats_json: str = Field(default="{}")  # {segments_coded, flagged, retries, tokens}


class Assignment(SQLModel, table=True):
    """A code applied to a segment by AI or human."""

    __tablename__ = "assignment"
    __table_args__ = (Index("ix_assignment_segment_status", "segment_id", "status"),)

    id: str = Field(primary_key=True, default_factory=new_id)
    segment_id: str = Field(foreign_key="segment.id", index=True)
    code_id: str = Field(foreign_key="code.id")
    run_id: str | None = Field(default=None, foreign_key="codingrun.id")  # null for human-created
    source: str  # ai | human
    rationale: str = Field(default="")
    confidence: float | None = None  # 0..1; null for human
    status: str = Field(default="pending")  # pending | approved | rejected | edited
    created_at: datetime = Field(default_factory=utcnow_naive)


class AuditEvent(SQLModel, table=True):
    """Append-only audit record; UPDATE/DELETE blocked by SQLite triggers."""

    __tablename__ = "auditevent"

    id: str = Field(primary_key=True, default_factory=new_id)
    project_id: str = Field(foreign_key="project.id", index=True)
    ts: datetime = Field(default_factory=utcnow_naive)
    actor: str  # ai | human | system
    event_type: str  # taxonomy in 04_DATA_MODEL section 1
    payload_json: str = Field(default="{}")  # prompts hashed when provider is remote


class MemoryItem(SQLModel, table=True):
    """A remembered correction/definition/note; vector copy lives in ChromaDB."""

    __tablename__ = "memoryitem"

    id: str = Field(primary_key=True, default_factory=new_id)
    project_id: str = Field(foreign_key="project.id", index=True)
    kind: str  # correction | definition | note
    text: str
    chroma_id: str = ""
    created_at: datetime = Field(default_factory=utcnow_naive)
