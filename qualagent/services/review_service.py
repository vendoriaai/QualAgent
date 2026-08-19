"""Human review of AI assignments: approve / reject / edit (FR-7, TAD §8).

Every decision is an explicit human action: the assignment status moves out of
``pending``, an audit event records who decided what, and code edits also
write a ``MemoryItem(kind=correction)`` so future coding runs learn from the
reassignment (TAD D7).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, col, select

from qualagent.domain.errors import Conflict, SegmentNotFound, ValidationError
from qualagent.domain.models import Assignment, Code, Segment
from qualagent.services.audit_service import AuditService
from qualagent.services.memory_service import MemoryService


@dataclass(frozen=True)
class ReviewItem:
    """One assignment plus the context a reviewer needs to decide."""

    assignment: Assignment
    segment_text: str
    speaker: str | None
    code_name: str | None
    document_id: str


class ReviewService:
    """List and decide AI assignments."""

    def __init__(
        self,
        session: Session,
        audit: AuditService,
        memory: MemoryService | None = None,
    ) -> None:
        self._session = session
        self._audit = audit
        self._memory = memory

    # -- listing -----------------------------------------------------------

    def list_items(
        self,
        *,
        status: str | None = None,
        code: str | None = None,
        min_confidence: float = 0.0,
        max_confidence: float = 1.0,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReviewItem], int]:
        """Filtered assignment list with segment text, plus total count."""
        stmt = select(Assignment)
        if status is not None:
            stmt = stmt.where(col(Assignment.status) == status)
        if min_confidence > 0.0:
            stmt = stmt.where(col(Assignment.confidence) >= min_confidence)
        if max_confidence < 1.0:
            stmt = stmt.where(col(Assignment.confidence) <= max_confidence)
        if code is not None:
            code_row = self._session.exec(select(Code).where(col(Code.name) == code)).first()
            if code_row is None:
                raise ValidationError(f"Code '{code}' not found")
            stmt = stmt.where(col(Assignment.code_id) == code_row.id)
        total = len(self._session.exec(stmt).all())
        rows = self._session.exec(
            stmt.order_by(col(Assignment.created_at)).offset(offset).limit(limit)
        ).all()
        items = [self._to_item(a) for a in rows]
        return items, total

    def get(self, assignment_id: str) -> ReviewItem:
        row = self._session.get(Assignment, assignment_id)
        if row is None:
            raise ValidationError(f"Assignment {assignment_id} not found")
        return self._to_item(row)

    # -- decisions ----------------------------------------------------------

    def approve(self, assignment_id: str, *, note: str = "") -> Assignment:
        """Approve a pending assignment."""
        row = self._get_pending(assignment_id)
        row.status = "approved"
        self._save(row)
        self._audit.emit(
            "human",
            "assignment.approved",
            {"assignment_id": row.id, "note": note},
        )
        return row

    def reject(self, assignment_id: str, *, note: str = "") -> Assignment:
        """Reject a pending assignment (the code does not apply)."""
        row = self._get_pending(assignment_id)
        row.status = "rejected"
        self._save(row)
        self._audit.emit(
            "human",
            "assignment.rejected",
            {"assignment_id": row.id, "note": note},
        )
        return row

    def edit_code(
        self,
        assignment_id: str,
        *,
        new_code_name: str,
        note: str = "",
    ) -> Assignment:
        """Reassign a pending assignment to a different code.

        The old->new pair is written to memory as a correction so subsequent
        coding runs retrieve it in their prompts.
        """
        row = self._get_pending(assignment_id)
        old_code = self._code_name(row.code_id)
        new_code = self._session.exec(select(Code).where(col(Code.name) == new_code_name)).first()
        if new_code is None:
            raise ValidationError(f"Code '{new_code_name}' not found")
        row.code_id = new_code.id
        row.status = "edited"
        self._save(row)
        if self._memory is not None:
            self._memory.add_correction("", old_code=old_code, new_code=new_code_name, note=note)
        self._audit.emit(
            "human",
            "assignment.edited",
            {
                "assignment_id": row.id,
                "old_code": old_code,
                "new_code": new_code_name,
                "note": note,
            },
        )
        return row

    # -- internals ----------------------------------------------------------

    def _get_pending(self, assignment_id: str) -> Assignment:
        row = self._session.get(Assignment, assignment_id)
        if row is None:
            raise ValidationError(f"Assignment {assignment_id} not found")
        if row.status != "pending":
            raise Conflict(f"Assignment already decided (status={row.status})")
        return row

    def _save(self, row: Assignment) -> None:
        self._session.add(row)
        self._session.commit()
        self._session.refresh(row)

    def _code_name(self, code_id: str) -> str | None:
        code = self._session.get(Code, code_id)
        return code.name if code else None

    def _to_item(self, a: Assignment) -> ReviewItem:
        segment = self._session.get(Segment, a.segment_id)
        if segment is None:
            raise SegmentNotFound(f"Segment {a.segment_id} not found")
        from qualagent.domain.models import Document

        doc = self._session.get(Document, segment.document_id)
        text = doc.raw_text[segment.start_offset : segment.end_offset] if doc else ""
        return ReviewItem(
            assignment=a,
            segment_text=text,
            speaker=segment.speaker,
            code_name=self._code_name(a.code_id),
            document_id=segment.document_id,
        )
