"""Entity CRUD, constraints, WAL mode, and append-only trigger tests."""

from __future__ import annotations

import pytest
from qualagent.domain.models import (
    Assignment,
    AuditEvent,
    Code,
    Codebook,
    CodingRun,
    Document,
    MemoryItem,
    Project,
    Segment,
)
from qualagent.storage.db import init_db, make_engine
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session


class TestEnginePragmas:
    def test_wal_mode_enabled(self, db_engine) -> None:
        with db_engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert str(mode).lower() == "wal"

    def test_triggers_installed(self, db_engine) -> None:
        with db_engine.connect() as conn:
            names = {
                row[0]
                for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
            }
        assert {"audit_no_update", "audit_no_delete"} <= names


class TestAuditAppendOnly:
    def test_update_blocked(self, session: Session, project_row: Project) -> None:
        event = AuditEvent(project_id=project_row.id, actor="system", event_type="x.y")
        session.add(event)
        session.commit()
        with pytest.raises(Exception, match="append-only"):
            session.execute(
                text("UPDATE auditevent SET event_type = 'tampered' WHERE id = :id"),
                {"id": event.id},
            )
        session.rollback()

    def test_delete_blocked(self, session: Session, project_row: Project) -> None:
        event = AuditEvent(project_id=project_row.id, actor="system", event_type="x.y")
        session.add(event)
        session.commit()
        with pytest.raises(Exception, match="append-only"):
            session.execute(text("DELETE FROM auditevent"), {})
        session.rollback()


class TestConstraints:
    def test_project_name_unique(self, session: Session) -> None:
        session.add(Project(name="dup"))
        session.commit()
        session.add(Project(name="dup"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_segment_doc_index_unique(self, session: Session, project_row: Project) -> None:
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="hello",
        )
        session.add(doc)
        session.commit()
        for idx in (0, 0):
            session.add(
                Segment(
                    document_id=doc.id,
                    index=idx,
                    start_offset=0,
                    end_offset=5,
                    segmenter_version="sentence@1",
                )
            )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_codebook_project_version_unique(self, session: Session, project_row: Project) -> None:
        for _ in range(2):
            session.add(Codebook(project_id=project_row.id, version=1))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_code_name_unique_per_codebook(self, session: Session, project_row: Project) -> None:
        book = Codebook(project_id=project_row.id, version=1)
        session.add(book)
        session.commit()
        for _ in range(2):
            session.add(Code(codebook_id=book.id, name="same_code"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


class TestEntityRoundtrip:
    def test_document_segment_derivation(self, session: Session, project_row: Project) -> None:
        raw = "The teacher explained the grammar point."
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="1" * 64,
            mime="text/plain",
            raw_text=raw,
        )
        session.add(doc)
        session.commit()
        seg = Segment(
            document_id=doc.id,
            index=0,
            start_offset=0,
            end_offset=21,
            segmenter_version="sentence@1",
        )
        session.add(seg)
        session.commit()
        session.refresh(seg)
        assert doc.raw_text[seg.start_offset : seg.end_offset] == "The teacher explained"
        assert seg.speaker is None

    def test_full_lifecycle_rows(self, session: Session, project_row: Project) -> None:
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="2" * 64,
            mime="text/plain",
            raw_text="abc",
        )
        session.add(doc)
        session.commit()
        seg = Segment(
            document_id=doc.id,
            index=0,
            start_offset=0,
            end_offset=3,
            segmenter_version="utterance@1",
        )
        session.add(seg)
        book = Codebook(project_id=project_row.id, version=1)
        session.add(book)
        session.commit()
        code = Code(codebook_id=book.id, name="activation")
        session.add(code)
        run = CodingRun(
            project_id=project_row.id,
            pack="van_leeuwen@1.0.0",
            codebook_version=1,
            model="ollama/qwen3:32b",
        )
        session.add(run)
        session.commit()
        asg = Assignment(
            segment_id=seg.id,
            code_id=code.id,
            run_id=run.id,
            source="ai",
            rationale="agent of material process",
            confidence=0.9,
        )
        mem = MemoryItem(
            project_id=project_row.id,
            kind="correction",
            text="prefer activation",
            chroma_id="chroma-1",
        )
        session.add_all([asg, mem])
        session.commit()
        session.refresh(asg)
        session.refresh(mem)
        assert asg.status == "pending"
        assert mem.chroma_id == "chroma-1"
        assert run.temperature == 0.0


class TestReopenDb:
    def test_schema_and_triggers_persist(self, tmp_path) -> None:
        engine = make_engine(tmp_path / "persist.db")
        init_db(engine)
        engine.dispose()
        engine2 = make_engine(tmp_path / "persist.db")
        init_db(engine2)  # idempotent
        with engine2.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'")
            ).scalar()
        assert int(n) >= 2
        engine2.dispose()
