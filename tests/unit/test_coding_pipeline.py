"""Memory service tests (stubbed Chroma) and end-to-end coding run tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qualagent.config import QualAgentConfig
from qualagent.domain.models import Assignment, Document, Project, Segment
from qualagent.llm.fake import FakeProvider
from qualagent.packs.loader import load_pack
from qualagent.services.audit_service import AuditService
from qualagent.services.codebook_service import CodebookService
from qualagent.services.coding_service import CodingService
from qualagent.services.memory_service import MemoryService


class FakeCollection:
    """In-memory stand-in for a Chroma collection."""

    def __init__(self) -> None:
        self.docs: dict[str, str] = {}

    def count(self) -> int:
        return len(self.docs)

    def add(self, ids: list[str], documents: list[str], metadatas: list) -> None:
        for i, d in zip(ids, documents, strict=True):
            self.docs[i] = d

    def query(self, query_texts: list[str], n_results: int) -> dict:
        q = query_texts[0].lower()
        scored = sorted(
            self.docs.values(),
            key=lambda d: -sum(1 for w in d.lower().split() if w in q.split()),
        )
        return {"documents": [scored[:n_results]]}


class FakeChromaClient:
    def __init__(self) -> None:
        self.collection = FakeCollection()

    def get_or_create_collection(self, name: str, metadata: dict | None = None):
        return self.collection


@pytest.fixture
def memory(session, project_row: Project, tmp_path: Path) -> MemoryService:
    return MemoryService(session, project_row.id, tmp_path, client=FakeChromaClient())


class TestMemoryService:
    def test_correction_roundtrip(self, memory: MemoryService) -> None:
        item = memory.add_correction(
            "Prefer activation over passivation here", old_code="passivation", new_code="activation"
        )
        assert item.kind == "correction"
        assert item.chroma_id == item.id
        got = memory.retrieve("what about activation vs passivation")
        assert any("activation" in g for g in got)

    def test_definition_roundtrip(self, memory: MemoryService) -> None:
        memory.add_definition("wait_time", "Pauses the teacher makes after questions")
        got = memory.retrieve("teacher pause question wait")
        assert any("wait_time" in g for g in got)

    def test_empty_retrieval(self, memory: MemoryService) -> None:
        assert memory.retrieve("anything") == []

    def test_composed_text(self, memory: MemoryService) -> None:
        item = memory.add_correction("", old_code="a", new_code="b", note="be specific")
        assert "a -> b" in item.text and "be specific" in item.text

    def test_top_k_respected(self, session, project_row: Project, tmp_path: Path) -> None:
        client = FakeChromaClient()
        mem = MemoryService(session, project_row.id, tmp_path, client=client)
        for i in range(5):
            mem.add_correction(f"note number {i}")
        assert len(mem.retrieve("note number", k=2)) == 2


# ---------------------------------------------------------------------------
# End-to-end coding run (roadmap Phase 4 acceptance, fake LLM)
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(session, project_row: Project) -> dict[str, str]:
    """One document, three segments, a codebook with two codes."""
    raw = "I review vocabulary nightly. Group work felt hard. The pacing improved later."
    doc = Document(
        project_id=project_row.id,
        filename="a.txt",
        sha256="0" * 64,
        mime="text/plain",
        raw_text=raw,
    )
    session.add(doc)
    session.commit()
    offsets = [(0, 26), (27, 49), (50, 78)]
    for i, (s, e) in enumerate(offsets):
        session.add(
            Segment(
                document_id=doc.id,
                index=i,
                start_offset=s,
                end_offset=e,
                segmenter_version="sentence@1",
            )
        )
    session.commit()
    segs = list(session.exec(__import__("sqlmodel").select(Segment).order_by(Segment.index)))
    audit = AuditService(session, project_row.id)
    books = CodebookService(session, audit)
    book = books.ensure_codebook(project_row)
    books.add_code(book, name="review_routine", definition="Deliberate study routine")
    books.add_code(book, name="initial_anxiety", definition="Early difficulty feelings")
    return {
        "doc_id": doc.id,
        "seg_ids": [s.id for s in segs],
        "book_id": book.id,
    }


def _fake_response(seg_ids: list[str]) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "segment_id": seg_ids[0],
                    "assignments": [
                        {
                            "code": "review_routine",
                            "rationale": "nightly vocabulary review is a routine",
                            "confidence": 0.92,
                        }
                    ],
                    "uncodable": False,
                },
                {
                    "segment_id": seg_ids[1],
                    "assignments": [
                        {
                            "code": "initial_anxiety",
                            "rationale": "'felt hard' expresses early difficulty",
                            "confidence": 0.8,
                        }
                    ],
                    "uncodable": False,
                },
                {
                    "segment_id": seg_ids[2],
                    "assignments": [],
                    "uncodable": True,
                },
            ],
            "new_code_proposals": [
                {
                    "name": "pacing_change",
                    "definition": "Mentions pacing improving",
                    "example_segment_id": seg_ids[2],
                }
            ],
        }
    )


class TestCodingRun:
    def test_full_run_persists_assignments_and_audits(
        self, session, project_row: Project, seeded: dict[str, str]
    ) -> None:
        audit = AuditService(session, project_row.id)
        books = CodebookService(session, audit)
        provider = FakeProvider([_fake_response(seeded["seg_ids"])])
        service = CodingService(session, audit, books, None, provider, QualAgentConfig())
        pack = load_pack("open_coding")
        run = service.start_run(project_row, pack)

        assert run.status == "completed"
        stats = service.run_stats(run)
        assert stats["assignments"] == 2
        assert stats["batches"] == 1
        assert stats["suggestions"] == 1

        assignments = list(session.exec(__import__("sqlmodel").select(Assignment)))
        assert len(assignments) == 2
        assert all(a.source == "ai" and a.status == "pending" for a in assignments)
        assert all(a.run_id == run.id for a in assignments)
        assert any(a.rationale.startswith("nightly") for a in assignments)

        events, _total = audit.list_events()
        types = {e.event_type for e in events}
        assert {
            "run.started",
            "llm.call",
            "run.batch_completed",
            "run.completed",
            "codebook.created",
            "code.created",
        } <= types

        book = books.get(seeded["book_id"])
        assert "suggestion_pacing_change" in [c.name for c in books.codes(book)]

    def test_double_validation_failure_flags_segments(
        self, session, project_row: Project, seeded: dict[str, str]
    ) -> None:
        audit = AuditService(session, project_row.id)
        books = CodebookService(session, audit)
        provider = FakeProvider(["garbage", "more garbage"])
        service = CodingService(session, audit, books, None, provider, QualAgentConfig())
        run = service.start_run(project_row, load_pack("open_coding"))
        assert run.status == "completed"  # batch flagged, run survives
        stats = service.run_stats(run)
        assert stats["flagged"] == 3
        assert list(session.exec(__import__("sqlmodel").select(Assignment))) == []

    def test_batching_by_count(self, session, project_row: Project, seeded: dict[str, str]) -> None:
        from qualagent.services.coding_service import CodingService as CS

        class Dummy:
            start_offset = 0
            end_offset = 40

        segments = [Dummy() for _ in range(5)]
        batches = CS._batch_segments(segments, max_count=2, token_budget=10_000)
        assert [len(b) for b in batches] == [2, 2, 1]

    def test_batching_by_token_budget(self) -> None:
        from qualagent.services.coding_service import CodingService as CS

        class Dummy:
            def __init__(self, n: int) -> None:
                self.start_offset = 0
                self.end_offset = n * 4  # n tokens

        segments = [Dummy(30), Dummy(30), Dummy(30)]
        batches = CS._batch_segments(segments, max_count=10, token_budget=70)
        assert [len(b) for b in batches] == [2, 1]

    def test_no_segments_raises(self, session, project_row: Project) -> None:
        audit = AuditService(session, project_row.id)
        books = CodebookService(session, audit)
        provider = FakeProvider(["{}"])
        service = CodingService(session, audit, books, None, provider, QualAgentConfig())
        from qualagent.domain.errors import ValidationError

        with pytest.raises(ValidationError, match="No segments"):
            service.start_run(project_row, load_pack("open_coding"))

    def test_document_filter(self, session, project_row: Project, seeded) -> None:
        audit = AuditService(session, project_row.id)
        books = CodebookService(session, audit)
        provider = FakeProvider([_fake_response(seeded["seg_ids"])])
        service = CodingService(session, audit, books, None, provider, QualAgentConfig())
        run = service.start_run(project_row, load_pack("open_coding"), document_id=seeded["doc_id"])
        assert run.status == "completed"

        from qualagent.domain.errors import DocumentNotFound

        with pytest.raises(DocumentNotFound):
            service.start_run(project_row, load_pack("open_coding"), document_id="missing")
