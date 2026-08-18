"""Codebook lifecycle: draft -> refined -> locked (FR-6).

Locking snapshots a version: subsequent coding runs reference the locked
version, and no codes may be added or changed in it. New-code proposals from
coding runs are stored as draft suggestions in a new draft version — never
auto-applied (TAD section 6 step 5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlmodel import Session, col, select

from qualagent.domain.errors import CodebookLocked, Conflict, ValidationError
from qualagent.domain.models import Code, Codebook, Project
from qualagent.services.audit_service import AuditService

#: Codes proposed by AI but not yet accepted by a human.
SUGGESTION_PREFIX = "suggestion_"


@dataclass(frozen=True)
class CodeSuggestion:
    """A new-code proposal awaiting human decision."""

    name: str
    definition: str = ""
    example_segment_id: str | None = None


class CodebookService:
    """Manage codebook versions and their codes."""

    def __init__(self, session: Session, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # -- version lifecycle ------------------------------------------------

    def ensure_codebook(self, project: Project) -> Codebook:
        """Return the project's current codebook, creating version 1 if needed."""
        book = self._latest(project.id)
        if book is not None:
            return book
        return self.create_version(project, copied_from=None)

    def create_version(self, project: Project, *, copied_from: Codebook | None) -> Codebook:
        """Create a new draft codebook version, optionally copying codes."""
        latest = self._latest(project.id)
        next_version = (latest.version + 1) if latest else 1
        book = Codebook(project_id=project.id, version=next_version, status="draft")
        self._session.add(book)
        self._session.commit()
        self._session.refresh(book)
        if copied_from is not None:
            for code in self._codes_of(copied_from.id):
                clone = Code(
                    codebook_id=book.id,
                    parent_id=None,
                    name=code.name,
                    definition=code.definition,
                    inclusion_criteria=code.inclusion_criteria,
                    exclusion_criteria=code.exclusion_criteria,
                    example_segment_ids=code.example_segment_ids,
                )
                self._session.add(clone)
            self._session.commit()
        project.active_codebook_version = book.version
        self._session.add(project)
        self._session.commit()
        self._audit.emit(
            "system",
            "codebook.created",
            {
                "codebook_id": book.id,
                "version": book.version,
                "copied_from": copied_from.version if copied_from else None,
            },
        )
        return book

    def refine(self, project: Project, *, from_suggestions: bool = False) -> Codebook:
        """Create a refined copy of the latest codebook (Braun & Clarke phase 3).

        Args:
            from_suggestions: Also promote accepted suggestions into real codes.
        """
        latest = self._latest(project.id)
        if latest is None:
            raise ValidationError("No codebook exists yet")
        self._assert_not_locked(latest)
        new_book = self.create_version(project, copied_from=latest)
        new_book.status = "refined"
        self._session.add(new_book)
        self._session.commit()
        promoted = 0
        if from_suggestions:
            promoted = self._promote_suggestions(new_book)
        self._audit.emit(
            "ai",
            "codebook.refined",
            {
                "codebook_id": new_book.id,
                "version": new_book.version,
                "from_suggestions": from_suggestions,
                "promoted": promoted,
            },
        )
        return new_book

    def lock(self, project: Project, version: int | None = None) -> Codebook:
        """Lock a codebook version; snapshots it as the project's active version."""
        book = self._by_version(project.id, version)
        if book is None:
            raise ValidationError(f"Codebook version {version} not found")
        if book.status == "locked":
            raise CodebookLocked(f"Codebook version {book.version} is already locked")
        book.status = "locked"
        from qualagent.domain.models import utcnow_naive

        book.locked_at = utcnow_naive()
        project.active_codebook_version = book.version
        self._session.add(book)
        self._session.add(project)
        self._session.commit()
        self._session.refresh(book)
        self._audit.emit(
            "human",
            "codebook.locked",
            {
                "codebook_id": book.id,
                "version": book.version,
                "code_count": len(self._codes_of(book.id)),
            },
        )
        return book

    # -- codes -------------------------------------------------------------

    def add_code(
        self,
        book: Codebook,
        *,
        name: str,
        definition: str = "",
        inclusion_criteria: str = "",
        exclusion_criteria: str = "",
        parent_id: str | None = None,
        example_segment_ids: list[str] | None = None,
    ) -> Code:
        """Add a code to a draft/refined codebook (never to a locked one)."""
        self._assert_not_locked(book)
        if any(c.name == name for c in self._codes_of(book.id)):
            raise Conflict(f"Code '{name}' already exists in this codebook")
        code = Code(
            codebook_id=book.id,
            parent_id=parent_id,
            name=name,
            definition=definition,
            inclusion_criteria=inclusion_criteria,
            exclusion_criteria=exclusion_criteria,
            example_segment_ids=json.dumps(example_segment_ids or []),
        )
        self._session.add(code)
        self._session.commit()
        self._session.refresh(code)
        self._audit.emit(
            "human",
            "code.created",
            {"codebook_id": book.id, "code_id": code.id, "name": name},
        )
        return code

    def create_draft_code(self, book: Codebook, *, name: str, definition: str = "") -> Code:
        """Create a draft code used by an AI assignment during initial coding.

        Unlike :meth:`add_suggestions`, the code is immediately usable for
        assignments (initial coding produces the codebook), but it lives in a
        draft codebook the human can refine, edit, or discard.
        """
        self._assert_not_locked(book)
        existing = {c.name for c in self._codes_of(book.id)}
        base = name
        suffix = 2
        unique = name
        while unique in existing:
            unique = f"{base}_{suffix}"
            suffix += 1
        code = Code(codebook_id=book.id, name=unique, definition=definition)
        self._session.add(code)
        self._session.commit()
        self._session.refresh(code)
        self._audit.emit(
            "ai",
            "code.created",
            {"codebook_id": book.id, "code_id": code.id, "name": unique, "origin": "assignment"},
        )
        return code

    def add_suggestions(self, book: Codebook, suggestions: list[CodeSuggestion]) -> int:
        """Record AI new-code proposals as prefixed draft codes (not applied)."""
        self._assert_not_locked(book)
        added = 0
        existing = {c.name for c in self._codes_of(book.id)}
        for suggestion in suggestions:
            sname = f"{SUGGESTION_PREFIX}{suggestion.name}"
            if sname in existing or suggestion.name in existing:
                continue
            self._session.add(
                Code(
                    codebook_id=book.id,
                    name=sname,
                    definition=suggestion.definition,
                    example_segment_ids=json.dumps(
                        [suggestion.example_segment_id] if suggestion.example_segment_id else []
                    ),
                )
            )
            existing.add(sname)
            added += 1
        if added:
            self._session.commit()
            self._audit.emit(
                "ai",
                "code.created",
                {"codebook_id": book.id, "suggestions": added, "kind": "suggestion"},
            )
        return added

    def _promote_suggestions(self, book: Codebook) -> int:
        promoted = 0
        for code in self._codes_of(book.id):
            if code.name.startswith(SUGGESTION_PREFIX):
                code.name = code.name.removeprefix(SUGGESTION_PREFIX)
                self._session.add(code)
                promoted += 1
        if promoted:
            self._session.commit()
        return promoted

    # -- queries -----------------------------------------------------------

    def get(self, codebook_id: str) -> Codebook:
        book = self._session.get(Codebook, codebook_id)
        if book is None:
            raise ValidationError(f"Codebook {codebook_id} not found")
        return book

    def get_by_version(self, project_id: str, version: int | None) -> Codebook:
        book = self._by_version(project_id, version)
        if book is None:
            raise ValidationError(
                f"Codebook version {version if version else '(latest)'} not found"
            )
        return book

    def list_codebooks(self, project_id: str) -> list[Codebook]:
        return list(
            self._session.exec(
                select(Codebook)
                .where(col(Codebook.project_id) == project_id)
                .order_by(col(Codebook.version))
            )
        )

    def codes(self, book: Codebook, *, include_suggestions: bool = True) -> list[Code]:
        codes = self._codes_of(book.id)
        if include_suggestions:
            return codes
        return [c for c in codes if not c.name.startswith(SUGGESTION_PREFIX)]

    def code_map(self, book: Codebook) -> dict[str, Code]:
        """Map code name -> Code for prompt building and assignment lookup."""
        return {c.name: c for c in self._codes_of(book.id)}

    # -- internals ----------------------------------------------------------

    def _latest(self, project_id: str) -> Codebook | None:
        books = self._session.exec(
            select(Codebook)
            .where(col(Codebook.project_id) == project_id)
            .order_by(col(Codebook.version))
        ).all()
        return books[-1] if books else None

    def _by_version(self, project_id: str, version: int | None) -> Codebook | None:
        stmt = select(Codebook).where(col(Codebook.project_id) == project_id)
        if version is None:
            stmt = stmt.order_by(col(Codebook.version).desc())
            return self._session.exec(stmt).first()
        stmt = stmt.where(col(Codebook.version) == version)
        return self._session.exec(stmt).first()

    def _codes_of(self, codebook_id: str) -> list[Code]:
        return list(
            self._session.exec(
                select(Code).where(col(Code.codebook_id) == codebook_id).order_by(col(Code.name))
            )
        )

    def _assert_not_locked(self, book: Codebook) -> None:
        if book.status == "locked":
            raise CodebookLocked(
                f"Codebook v{book.version} is locked; create a new version to edit"
            )
