"""IRR and export service tests, including the fidelity guarantee."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from qualagent.domain.models import Assignment, Code, Codebook, Document, Project, Segment
from qualagent.services.audit_service import AuditService
from qualagent.services.export_service import ExportService
from qualagent.services.irr_service import IRRCalculator


@pytest.fixture
def seeded(session, project_row: Project) -> dict[str, str]:
    """Two docs, three segments, two codes, mixed decision statuses."""
    doc1 = Document(
        project_id=project_row.id,
        filename="a.txt",
        mime="text/plain",
        raw_text="One two three.Four five six.",
        sha256="a" * 64,
    )
    doc2 = Document(
        project_id=project_row.id,
        filename="b.txt",
        mime="text/plain",
        raw_text="Seven eight nine.",
        sha256="b" * 64,
    )
    session.add_all([doc1, doc2])
    session.commit()
    s1 = Segment(
        document_id=doc1.id, index=0, start_offset=0, end_offset=13, segmenter_version="sentence@1"
    )
    s2 = Segment(
        document_id=doc1.id, index=1, start_offset=13, end_offset=26, segmenter_version="sentence@1"
    )
    s3 = Segment(
        document_id=doc2.id, index=0, start_offset=0, end_offset=16, segmenter_version="sentence@1"
    )
    session.add_all([s1, s2, s3])
    session.commit()
    book = Codebook(project_id=project_row.id, version=1, status="draft")
    session.add(book)
    session.commit()
    ca = Code(codebook_id=book.id, name="cat_a", definition="A things")
    cb = Code(codebook_id=book.id, name="cat_b", definition="B things")
    session.add_all([ca, cb])
    session.commit()
    # AI assignments (pending)
    a1 = Assignment(segment_id=s1.id, code_id=ca.id, source="ai", confidence=0.9)
    a2 = Assignment(segment_id=s2.id, code_id=cb.id, source="ai", confidence=0.8)
    a3 = Assignment(segment_id=s3.id, code_id=ca.id, source="ai", confidence=0.7)
    # Human decisions on the same segments (source=human rows)
    h1 = Assignment(segment_id=s1.id, code_id=ca.id, source="human", status="approved")
    h2 = Assignment(segment_id=s2.id, code_id=ca.id, source="human", status="edited")
    h3 = Assignment(segment_id=s3.id, code_id=cb.id, source="human", status="rejected")
    session.add_all([a1, a2, a3, h1, h2, h3])
    session.commit()
    return {
        "s1": s1.id,
        "s2": s2.id,
        "s3": s3.id,
        "ca": ca.id,
        "cb": cb.id,
        "doc1": doc1.id,
        "doc2": doc2.id,
        "a1": a1.id,
    }


@pytest.fixture
def export_svc(session, project_row: Project) -> ExportService:
    return ExportService(session, AuditService(session, project_row.id))


class TestIRR:
    def test_overall_and_per_code(self, session, seeded: dict[str, str]) -> None:
        report = IRRCalculator(session).compute()
        assert report.n_compared == 3
        # AI: a b a / human: a a __rejected__ -> kappa between 0 and 1
        assert -1.0 <= report.overall_kappa <= 1.0
        assert "cat_a" in report.per_code and "__rejected__" in report.per_code

    def test_no_decisions(self, session, project_row: Project) -> None:
        report = IRRCalculator(session).compute()
        assert report.n_compared == 0

    def test_perfect_agreement(self, session, seeded: dict[str, str]) -> None:
        # Approve everything (human == AI).
        for a in list(session.exec(__import__("sqlmodel").select(Assignment))):
            if a.source == "human" and a.status == "edited":
                a.code_id = seeded["cb"]
                a.status = "approved"
                session.add(a)
        session.commit()
        # Make the rejected human row approved with AI's code.
        for a in list(session.exec(__import__("sqlmodel").select(Assignment))):
            if a.source == "human" and a.status == "rejected":
                a.status = "approved"
                a.code_id = seeded["ca"]
                session.add(a)
        session.commit()
        report = IRRCalculator(session).compute()
        assert report.overall_kappa > 0.9


class TestMatrixExport:
    def test_csv_roundtrip(self, export_svc: ExportService, seeded, tmp_path: Path) -> None:
        out = tmp_path / "matrix.csv"
        exported = export_svc.export_matrix(out)
        assert exported.path == out and exported.kind == "matrix"
        import csv

        with out.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        names = {r["code"] for r in rows}
        assert "cat_a" in names and "cat_b" in names
        by_code = {r["code"]: r for r in rows}
        # cat_a in a.txt: pending AI a1 + approved human h1 = 2 assignments counted
        assert by_code["cat_a"]["a.txt"] == "2"
        assert by_code["cat_b"]["a.txt"] == "1"

    def test_xlsx_with_detail_sheet(
        self, export_svc: ExportService, seeded, tmp_path: Path
    ) -> None:
        import pandas as pd

        out = tmp_path / "matrix.xlsx"
        export_svc.export_matrix(out, fmt="xlsx")
        matrix = pd.read_excel(out, sheet_name="matrix")
        assert "code" in matrix.columns
        detail = pd.read_excel(out, sheet_name="segments")
        assert {"segment_id", "code", "start_offset"} <= set(detail.columns)


class TestCodebookExport:
    def test_markdown_with_fidelity(
        self, export_svc: ExportService, session, seeded, tmp_path: Path
    ) -> None:
        """RELEASE-BLOCKING: every exported quote maps to exact source offsets."""
        # Approve an assignment so its quote becomes an example.
        a1 = session.get(Assignment, seeded["a1"])
        a1.status = "approved"
        session.add(a1)
        session.commit()
        out = tmp_path / "codebook.md"
        export_svc.export_codebook(out)
        content = out.read_text(encoding="utf-8")
        assert "## cat_a" in content and "Definition." in content
        # Fidelity: the quote line quotes text at the recorded offsets.
        doc1 = session.get(Document, seeded["doc1"])
        quote = doc1.raw_text[0:13]
        assert quote in content
        assert "[0:13]" in content

    def test_pdf(self, export_svc: ExportService, seeded, tmp_path: Path) -> None:
        out = tmp_path / "codebook.pdf"
        export_svc.export_codebook(out, fmt="pdf")
        assert out.read_bytes()[:5] == b"%PDF-"


class TestAuditExport:
    def test_jsonl_lines(
        self, export_svc: ExportService, seeded, tmp_path: Path, project_row: Project
    ) -> None:
        _session_of(export_svc)  # services share the fixture session
        AuditService(_session_of(export_svc), project_row.id).emit(
            "human", "assignment.approved", {"x": 1}
        )
        out = tmp_path / "audit.jsonl"
        export_svc.export_audit(out)
        lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert lines and all("event_type" in line for line in lines)
        assert any(line["event_type"] == "assignment.approved" for line in lines)

    def test_pdf(self, export_svc: ExportService, seeded, tmp_path: Path) -> None:
        out = tmp_path / "audit.pdf"
        export_svc.export_audit(out, fmt="pdf")
        assert out.read_bytes()[:5] == b"%PDF-"


class TestMethodsExport:
    def test_methods_paragraph(self, export_svc: ExportService, seeded, tmp_path: Path) -> None:
        out = tmp_path / "methods.md"
        export_svc.export_methods(out)
        text = out.read_text(encoding="utf-8")
        assert "QualAgent" in text
        assert "kappa" in text and "audit" in text


def _session_of(svc: ExportService):
    return svc._session
