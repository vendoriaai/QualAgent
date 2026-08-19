"""Review service tests: list filters, approve/reject/edit, memory writes."""

from __future__ import annotations

import pytest
from qualagent.domain.errors import Conflict, ValidationError
from qualagent.domain.models import Assignment, Code, Codebook, Document, Project, Segment
from qualagent.services.audit_service import AuditService
from qualagent.services.memory_service import MemoryService
from qualagent.services.review_service import ReviewService


@pytest.fixture
def seeded(session, project_row: Project) -> dict[str, str]:
    """One doc, two segments, two codes, two pending AI assignments."""
    doc = Document(
        project_id=project_row.id,
        filename="t.txt",
        mime="text/plain",
        raw_text="Alpha beta gamma.Delta epsilon!",
        sha256="x" * 64,
    )
    session.add(doc)
    session.commit()
    seg1 = Segment(
        document_id=doc.id, index=0, start_offset=0, end_offset=17, segmenter_version="sentence@1"
    )
    seg2 = Segment(
        document_id=doc.id, index=1, start_offset=17, end_offset=31, segmenter_version="sentence@1"
    )
    session.add_all([seg1, seg2])
    session.commit()
    book = Codebook(project_id=project_row.id, version=1, status="draft")
    session.add(book)
    session.commit()
    c1 = Code(codebook_id=book.id, name="code_a", definition="A")
    c2 = Code(codebook_id=book.id, name="code_b", definition="B")
    session.add_all([c1, c2])
    session.commit()
    a1 = Assignment(segment_id=seg1.id, code_id=c1.id, source="ai", rationale="r1", confidence=0.9)
    a2 = Assignment(segment_id=seg2.id, code_id=c2.id, source="ai", rationale="r2", confidence=0.4)
    session.add_all([a1, a2])
    session.commit()
    return {
        "seg1": seg1.id,
        "seg2": seg2.id,
        "a1": a1.id,
        "a2": a2.id,
        "c1": c1.id,
        "c2": c2.id,
        "doc": doc.id,
    }


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, str] = {}

    def count(self) -> int:
        return len(self.docs)

    def add(self, ids, documents, metadatas=None) -> None:
        for i, d in zip(ids, documents, strict=True):
            self.docs[i] = d

    def query(self, query_texts, n_results):
        return {"documents": [list(self.docs.values())[:n_results]]}


class FakeClient:
    def __init__(self) -> None:
        self.collection = FakeCollection()

    def get_or_create_collection(self, name, metadata=None):
        return self.collection


@pytest.fixture
def memory(session, project_row: Project) -> MemoryService:
    return MemoryService(
        session,
        project_row.id,
        session.bind is not None and __import__("pathlib").Path("."),
        client=FakeClient(),
    )


@pytest.fixture
def svc(session, project_row: Project, memory) -> ReviewService:
    return ReviewService(session, AuditService(session, project_row.id), memory)


class TestList:
    def test_list_pending(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        items, total = svc.list_items(status="pending")
        assert total == 2
        assert all(i.assignment.status == "pending" for i in items)

    def test_confidence_filter(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        _, total = svc.list_items(min_confidence=0.5)
        assert total == 1

    def test_code_filter(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        items, total = svc.list_items(code="code_a")
        assert total == 1
        assert items[0].assignment.id == seeded["a1"]

    def test_code_filter_unknown(self, svc: ReviewService) -> None:
        with pytest.raises(ValidationError):
            svc.list_items(code="nope")

    def test_segment_text_roundtrip(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        item = svc.get(seeded["a1"])
        assert item.segment_text == "Alpha beta gamma."


class TestDecide:
    def test_approve(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        row = svc.approve(seeded["a1"], note="ok")
        assert row.status == "approved"
        again = svc.get(seeded["a1"])
        assert again.assignment.status == "approved"

    def test_reject(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        row = svc.reject(seeded["a2"])
        assert row.status == "rejected"

    def test_edit_moves_code_and_writes_memory(
        self, svc: ReviewService, seeded: dict[str, str], session, memory
    ) -> None:
        row = svc.edit_code(seeded["a1"], new_code_name="code_b", note="fits B better")
        assert row.status == "edited" and row.code_id == seeded["c2"]
        items = memory.list_items(kind="correction")
        assert len(items) == 1
        assert "code_a" in items[0].text and "code_b" in items[0].text

    def test_edit_unknown_code(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        with pytest.raises(ValidationError):
            svc.edit_code(seeded["a1"], new_code_name="ghost")

    def test_double_decision_conflict(self, svc: ReviewService, seeded: dict[str, str]) -> None:
        svc.approve(seeded["a1"])
        with pytest.raises(Conflict):
            svc.reject(seeded["a1"])

    def test_audit_events_emitted(
        self, svc: ReviewService, seeded: dict[str, str], session, project_row
    ) -> None:
        svc.approve(seeded["a1"])
        svc.reject(seeded["a2"])
        events, _ = AuditService(session, project_row.id).list_events(limit=50)
        types = {e.event_type for e in events}
        assert {"assignment.approved", "assignment.rejected"} <= types
