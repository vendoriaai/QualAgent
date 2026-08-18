"""Ingestion parser and service tests (all five formats)."""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from qualagent.domain.errors import DocumentNotFound, ValidationError
from qualagent.services.audit_service import AuditService
from qualagent.services.ingestion_service import (
    IngestionService,
    detect_mime,
    normalize_text,
    parse_bytes,
)
from qualagent.services.segmentation_service import SegmentationService
from qualagent.storage.files import FileStore


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    from tests.fixtures import make_fixtures

    return make_fixtures(tmp_path / "fx")


class TestParsers:
    def test_txt(self, fixtures: dict[str, Path]) -> None:
        text = parse_bytes(fixtures["txt"].read_bytes(), "text/plain", fixtures["txt"])
        assert "Interviewer:" in text and "Teacher:" in text

    def test_docx(self, fixtures: dict[str, Path]) -> None:
        text = parse_bytes(
            fixtures["docx"].read_bytes(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            fixtures["docx"],
        )
        assert "Thanks for joining" in text

    def test_pdf(self, fixtures: dict[str, Path]) -> None:
        text = parse_bytes(fixtures["pdf"].read_bytes(), "application/pdf", fixtures["pdf"])
        assert "grammar" in text or "teaching" in text  # extractors vary in spacing

    def test_srt_strips_timestamps(self, fixtures: dict[str, Path]) -> None:
        text = parse_bytes(fixtures["srt"].read_bytes(), "application/x-subrip", fixtures["srt"])
        assert "-->" not in text
        assert "00:00" not in text
        assert "corrective feedback" in text

    def test_csv_speaker_lines(self, fixtures: dict[str, Path]) -> None:
        text = parse_bytes(fixtures["csv"].read_bytes(), "text/csv", fixtures["csv"])
        lines = text.split("\n")
        assert lines[0] == "Interviewer: Thanks for joining. Can you describe your teaching?"
        assert lines[3] == "Teacher: They appreciate it, especially when it is immediate and clear."

    def test_csv_requires_text_column(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.csv"
        bad.write_text("name,age\nA,1\n", encoding="utf-8")
        with pytest.raises(ValidationError, match="text"):
            parse_bytes(bad.read_bytes(), "text/csv", bad)

    def test_unsupported_mime(self, tmp_path: Path) -> None:
        f = tmp_path / "file.zip"
        f.write_bytes(b"PK")
        with pytest.raises(ValidationError, match="Unsupported"):
            parse_bytes(f.read_bytes(), "application/zip", f)

    def test_nfc_normalization(self) -> None:
        # "é" as e + combining accent (NFD) normalizes to precomposed (NFC)
        nfd = "cafe\u0301"
        assert normalize_text(nfd) == unicodedata.normalize("NFC", nfd)

    def test_crlf_normalized(self) -> None:
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"

    def test_detect_mime(self, tmp_path: Path) -> None:
        assert detect_mime(tmp_path / "a.txt") == "text/plain"
        assert detect_mime(tmp_path / "a.srt") == "application/x-subrip"
        assert detect_mime(tmp_path / "a.docx").endswith("wordprocessingml.document")
        assert detect_mime(tmp_path / "a.pdf") == "application/pdf"
        assert detect_mime(tmp_path / "a.csv") == "text/csv"


class TestIngestionService:
    @pytest.fixture
    def ingestion(self, session, tmp_path: Path, project_row) -> IngestionService:
        audit = AuditService(session, project_row.id)
        return IngestionService(
            session, FileStore(tmp_path / "proj"), audit, SegmentationService(session)
        )

    def test_ingest_full_flow(
        self, ingestion: IngestionService, session, project_row, tmp_path: Path
    ) -> None:
        src = tmp_path / "src.txt"
        src.write_text("First sentence here. Second sentence follows.", encoding="utf-8")
        document, segments = ingestion.ingest_file(project_row.id, src)
        assert document.filename == "src.txt"
        assert len(document.sha256) == 64
        assert len(segments) == 2
        events, total = AuditService(session, project_row.id).list_events(
            event_type="document.imported"
        )
        assert total == 1
        payload = AuditService.payload_of(events[0])
        assert payload["document_id"] == document.id
        assert payload["segment_count"] == 2

    def test_ingest_all_formats(
        self, ingestion: IngestionService, project_row, fixtures: dict[str, Path]
    ) -> None:
        for path in fixtures.values():
            document, segments = ingestion.ingest_file(project_row.id, path)
            assert document.raw_text.strip()
            assert len(segments) >= 1
            for seg in segments:
                assert document.raw_text[seg.start_offset : seg.end_offset].strip()

    def test_ingest_empty_file_rejected(
        self, ingestion: IngestionService, project_row, tmp_path: Path
    ) -> None:
        empty = tmp_path / "empty.txt"
        empty.write_text("   \n", encoding="utf-8")
        with pytest.raises(ValidationError, match="no text content"):
            ingestion.ingest_file(project_row.id, empty)

    def test_resegment_replaces(
        self, ingestion: IngestionService, session, project_row, tmp_path: Path
    ) -> None:
        src = tmp_path / "turns.txt"
        src.write_text("Interviewer: Hello there.\nTeacher: Hi, good to be here.", encoding="utf-8")
        document, _ = ingestion.ingest_file(project_row.id, src, strategy="sentence")
        new_segments = ingestion.resegment(document.id, "turns")
        assert len(new_segments) == 2
        assert new_segments[0].speaker == "Interviewer"
        events, _ = AuditService(session, project_row.id).list_events(
            event_type="document.resegmented"
        )
        assert len(events) == 1

    def test_list_documents_paginated(
        self, ingestion: IngestionService, project_row, tmp_path: Path
    ) -> None:
        for i in range(3):
            src = tmp_path / f"doc{i}.txt"
            src.write_text(f"Document {i} content.", encoding="utf-8")
            ingestion.ingest_file(project_row.id, src)
        docs, total = ingestion.list_documents(project_row.id, limit=2, offset=0)
        assert total == 3 and len(docs) == 2
        docs2, _ = ingestion.list_documents(project_row.id, limit=2, offset=2)
        assert len(docs2) == 1

    def test_get_document_missing(self, ingestion: IngestionService) -> None:
        with pytest.raises(DocumentNotFound):
            ingestion.get_document("missing")

    def test_to_document_out(
        self, ingestion: IngestionService, project_row, tmp_path: Path
    ) -> None:
        src = tmp_path / "one.txt"
        src.write_text("A sentence.", encoding="utf-8")
        document, _ = ingestion.ingest_file(project_row.id, src)
        out = ingestion.to_document_out(document)
        assert out["segment_count"] == 1
        assert out["filename"] == "one.txt"
