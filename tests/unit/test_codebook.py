"""Codebook lifecycle tests: draft -> refined -> locked, suggestions."""

from __future__ import annotations

import pytest
from qualagent.domain.errors import CodebookLocked, Conflict
from qualagent.domain.models import Project
from qualagent.services.audit_service import AuditService
from qualagent.services.codebook_service import CodebookService, CodeSuggestion


@pytest.fixture
def book_svc(session, project_row: Project) -> CodebookService:
    return CodebookService(session, AuditService(session, project_row.id))


class TestLifecycle:
    def test_ensure_creates_v1(self, book_svc: CodebookService, project_row: Project) -> None:
        book = book_svc.ensure_codebook(project_row)
        assert book.version == 1 and book.status == "draft"
        again = book_svc.ensure_codebook(project_row)
        assert again.id == book.id  # idempotent

    def test_refine_creates_next_version_with_codes(
        self, book_svc: CodebookService, project_row: Project
    ) -> None:
        book = book_svc.ensure_codebook(project_row)
        book_svc.add_code(book, name="confidence", definition="Self-efficacy talk")
        refined = book_svc.refine(project_row)
        assert refined.version == 2 and refined.status == "refined"
        names = [c.name for c in book_svc.codes(refined)]
        assert "confidence" in names
        assert project_row.active_codebook_version == 2

    def test_lock_snapshots_and_blocks_edits(
        self, book_svc: CodebookService, session, project_row: Project
    ) -> None:
        book = book_svc.ensure_codebook(project_row)
        book_svc.add_code(book, name="a_code")
        locked = book_svc.lock(project_row)
        assert locked.status == "locked" and locked.locked_at is not None
        assert project_row.active_codebook_version == 1
        with pytest.raises(CodebookLocked):
            book_svc.add_code(locked, name="another")
        with pytest.raises(CodebookLocked):
            book_svc.lock(project_row, version=1)

    def test_lock_specific_version(self, book_svc: CodebookService, project_row: Project) -> None:
        book_svc.ensure_codebook(project_row)
        v2 = book_svc.refine(project_row)
        locked = book_svc.lock(project_row, version=1)
        assert locked.version == 1
        assert v2.status == "refined"

    def test_duplicate_code_name_conflict(
        self, book_svc: CodebookService, project_row: Project
    ) -> None:
        book = book_svc.ensure_codebook(project_row)
        book_svc.add_code(book, name="dup")
        with pytest.raises(Conflict):
            book_svc.add_code(book, name="dup")


class TestSuggestions:
    def test_suggestions_prefixed_and_promoted(
        self, book_svc: CodebookService, project_row: Project
    ) -> None:
        book = book_svc.ensure_codebook(project_row)
        added = book_svc.add_suggestions(
            book,
            [
                CodeSuggestion(
                    name="wait_time", definition="Teacher pauses", example_segment_id="seg9"
                ),
                CodeSuggestion(name="wait_time", definition="dup ignored"),
            ],
        )
        assert added == 1
        names = [c.name for c in book_svc.codes(book)]
        assert "suggestion_wait_time" in names

        refined = book_svc.refine(project_row, from_suggestions=True)
        names = [c.name for c in book_svc.codes(refined)]
        assert "wait_time" in names and "suggestion_wait_time" not in names

    def test_codes_can_exclude_suggestions(
        self, book_svc: CodebookService, project_row: Project
    ) -> None:
        book = book_svc.ensure_codebook(project_row)
        book_svc.add_code(book, name="real")
        book_svc.add_suggestions(book, [CodeSuggestion(name="ghost", definition="")])
        real_only = [c.name for c in book_svc.codes(book, include_suggestions=False)]
        assert real_only == ["real"]


class TestAudit:
    def test_events_emitted(self, book_svc: CodebookService, session, project_row: Project) -> None:
        audit = AuditService(session, project_row.id)
        book = book_svc.ensure_codebook(project_row)
        book_svc.add_code(book, name="x")
        book_svc.refine(project_row)
        book_svc.lock(project_row)
        for event_type, actor in [
            ("codebook.created", "system"),
            ("code.created", "human"),
            ("codebook.refined", "ai"),
            ("codebook.locked", "human"),
        ]:
            events, total = audit.list_events(event_type=event_type)
            assert total >= 1, event_type
            assert events[0].actor == actor
