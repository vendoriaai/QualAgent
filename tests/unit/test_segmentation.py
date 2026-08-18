"""Segmentation strategy tests, including the release-blocking offset invariant."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qualagent.domain.errors import ValidationError
from qualagent.domain.models import Assignment, Code, Codebook, Document
from qualagent.services.segmentation_service import (
    STRATEGIES,
    SegmentationService,
    segment_text,
)


def invariant(raw_text: str, strategy: str) -> list[str]:
    """Assert the offset invariant and return the segment texts."""
    drafts = segment_text(raw_text, strategy)  # type: ignore[arg-type]
    texts = []
    for draft in drafts:
        seg_text = raw_text[draft.start_offset : draft.end_offset]
        assert seg_text.strip() != "", "segment must be non-empty"
        assert seg_text == seg_text.strip(), "segment must be exactly trimmed"
        texts.append(seg_text)
    assert texts, "at least one segment expected"
    return texts


class TestOffsetInvariant:
    def test_all_strategies_all_fixtures(self, tmp_path: Path) -> None:
        from tests.fixtures import make_fixtures

        fixtures = make_fixtures(tmp_path)
        from qualagent.services.ingestion_service import detect_mime, parse_bytes

        for path in fixtures.values():
            raw_text = parse_bytes(path.read_bytes(), detect_mime(path), path)
            for strategy in STRATEGIES:
                texts = invariant(raw_text, strategy)
                assert all(t for t in texts)


class TestSentenceStrategy:
    def test_basic_split(self) -> None:
        text = "One sentence here. Another follows! A third? Yes."
        assert invariant(text, "sentence") == [
            "One sentence here.",
            "Another follows!",
            "A third?",
            "Yes.",
        ]

    def test_abbreviations_do_not_split(self) -> None:
        text = "Dr. Smith arrived. We spoke with Mr. Jones about e.g. grammar."
        assert invariant(text, "sentence") == [
            "Dr. Smith arrived.",
            "We spoke with Mr. Jones about e.g. grammar.",
        ]

    def test_no_trailing_punctuation(self) -> None:
        text = "A full sentence. And a trailing fragment"
        assert invariant(text, "sentence") == ["A full sentence.", "And a trailing fragment"]

    def test_empty_text(self) -> None:
        assert segment_text("   \n  ", "sentence") == []


class TestParagraphStrategy:
    def test_blank_line_split(self) -> None:
        text = "First para, line one.\nStill first.\n\nSecond para here."
        assert invariant(text, "paragraph") == [
            "First para, line one.\nStill first.",
            "Second para here.",
        ]


class TestUtteranceStrategy:
    def test_line_split(self) -> None:
        text = "line one\nline two\n\nline three after blank"
        assert invariant(text, "utterance") == [
            "line one",
            "line two",
            "line three after blank",
        ]


class TestTurnsStrategy:
    def test_speaker_extraction(self) -> None:
        text = "Interviewer: Hello there.\nTeacher: Hi. Nice to meet you."
        drafts = segment_text(text, "turns")
        assert [d.speaker for d in drafts] == ["Interviewer", "Teacher"]
        invariant(text, "turns")

    def test_continuation_lines_join_current_turn(self) -> None:
        text = "Interviewer: Question one.\nSome extra context line.\nTeacher: Answer."
        drafts = segment_text(text, "turns")
        assert len(drafts) == 2
        assert drafts[1].speaker == "Teacher"
        invariant(text, "turns")

    def test_leading_non_turn_text(self) -> None:
        text = "Preamble line.\nInterviewer: First question."
        drafts = segment_text(text, "turns")
        assert drafts[0].speaker is None
        assert drafts[1].speaker == "Interviewer"
        invariant(text, "turns")

    def test_no_turns_falls_back_to_utterances(self) -> None:
        text = "just plain lines\nno speakers here"
        drafts = segment_text(text, "turns")
        assert all(d.speaker is None for d in drafts)
        assert len(drafts) == 2


class TestSegmentationService:
    def test_segment_persists_with_indices(self, session, project_row) -> None:
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="One. Two.",
        )
        session.add(doc)
        session.commit()
        svc = SegmentationService(session)
        segments = svc.segment(doc, "sentence")
        assert [s.index for s in segments] == [0, 1]
        assert all(s.segmenter_version == "sentence@1" for s in segments)
        for seg in segments:
            assert doc.raw_text[seg.start_offset : seg.end_offset].strip() != ""

    def test_second_segment_without_replace_raises(self, session, project_row) -> None:
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="One. Two.",
        )
        session.add(doc)
        session.commit()
        svc = SegmentationService(session)
        svc.segment(doc, "sentence")
        with pytest.raises(ValidationError):
            svc.segment(doc, "sentence")

    def test_replace_deletes_old_segments_and_assignments(self, session, project_row) -> None:
        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="One. Two.",
        )
        session.add(doc)
        session.commit()
        svc = SegmentationService(session)
        first = svc.segment(doc, "sentence")
        book = Codebook(project_id=project_row.id, version=1)
        session.add(book)
        session.commit()
        code = Code(codebook_id=book.id, name="activation")
        session.add(code)
        session.commit()
        session.add(Assignment(segment_id=first[0].id, code_id=code.id, source="human"))
        session.commit()

        second = svc.segment(doc, "turns", replace=True)
        assert len(second) == 1  # "One. Two." has no speaker -> single utterance-ish turn
        remaining_ids = [s.id for s in svc.list_segments(doc.id)[0]]
        assert remaining_ids == [second[0].id]
        # The assignment referencing the deleted segment is gone too.
        from sqlmodel import col, select

        leftover = session.exec(
            select(Assignment).where(col(Assignment.segment_id) == first[0].id)
        ).all()
        assert leftover == []

    def test_duplicate_index_blocked_by_constraint(self, session, project_row) -> None:
        from qualagent.domain.models import Segment

        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="One. Two.",
        )
        session.add(doc)
        session.commit()
        session.add(
            Segment(
                document_id=doc.id,
                index=0,
                start_offset=0,
                end_offset=3,
                segmenter_version="sentence@1",
            )
        )
        session.commit()
        session.add(
            Segment(
                document_id=doc.id,
                index=0,
                start_offset=5,
                end_offset=8,
                segmenter_version="sentence@1",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_unknown_strategy_rejected(self) -> None:
        with pytest.raises(ValidationError):
            segment_text("text", "word")  # type: ignore[arg-type]

    def test_get_segment_and_document(self, session, project_row) -> None:
        from qualagent.domain.errors import DocumentNotFound, SegmentNotFound

        doc = Document(
            project_id=project_row.id,
            filename="a.txt",
            sha256="0" * 64,
            mime="text/plain",
            raw_text="One sentence.",
        )
        session.add(doc)
        session.commit()
        svc = SegmentationService(session)
        seg = svc.segment(doc, "sentence")[0]
        assert svc.get_segment(seg.id).id == seg.id
        with pytest.raises(SegmentNotFound):
            svc.get_segment("missing")
        assert svc.get_document(doc.id, project_row.id).id == doc.id
        with pytest.raises(DocumentNotFound):
            svc.get_document("missing", project_row.id)
