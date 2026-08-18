"""Coding pipeline: deterministic batch orchestration (TAD D1, section 6).

Sequence per run: create CodingRun (snapshots pack + codebook) -> load segments
in batches (count or token budget) -> render prompt (pack + code definitions +
retrieved memories + segments) -> structured call with repair -> persist
assignments (status=pending) -> record proposals as suggestions -> audit each
batch. Validation-failed batches flag their segments for human review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, select

from qualagent.config import QualAgentConfig
from qualagent.domain.errors import DocumentNotFound, LLMValidationFailed, ValidationError
from qualagent.domain.models import (
    Assignment,
    Code,
    Codebook,
    CodingRun,
    Document,
    Project,
    Segment,
    utcnow_naive,
)
from qualagent.domain.schemas import CodingBatchResult
from qualagent.llm.base import LLMProvider
from qualagent.llm.structured import structured_call
from qualagent.packs.engine import PackEngine
from qualagent.packs.loader import Pack
from qualagent.services.audit_service import AuditService
from qualagent.services.codebook_service import CodebookService, CodeSuggestion
from qualagent.services.memory_service import MemoryService

#: chars-per-token heuristic for the batch budget (ADR-002).
CHARS_PER_TOKEN = 4


@dataclass
class BatchOutcome:
    """Result summary for one batch (drives run stats + audit)."""

    batch_index: int
    segment_ids: list[str]
    assignments_created: int = 0
    suggestions_added: int = 0
    flagged: bool = False
    retries: int = 0
    tokens: int = 0
    error: str | None = None


class CodingService:
    """Run methodology-faithful coding over a project's segments."""

    def __init__(
        self,
        session: Session,
        audit: AuditService,
        codebook_service: CodebookService | None,
        memory: MemoryService | None,
        provider: LLMProvider,
        config: QualAgentConfig,
    ) -> None:
        self._session = session
        self._audit = audit
        self._codebooks = codebook_service
        self._memory = memory
        self._provider = provider
        self._config = config

    # -- public API ---------------------------------------------------------

    def start_run(
        self,
        project: Project,
        pack: Pack,
        *,
        codebook_version: int | None = None,
        batch_size: int | None = None,
        document_id: str | None = None,
    ) -> CodingRun:
        """Create and execute a full coding run (blocking; streams via return)."""
        segments_precheck = self._session.exec(
            select(Segment)
            .join(Document, col(Segment.document_id) == col(Document.id))
            .where(col(Document.project_id) == project.id)
        ).first()
        if segments_precheck is None:
            raise ValidationError("No segments to code; import and segment first")
        if self._codebooks is None:
            raise ValidationError("CodingService requires a CodebookService to run")
        codebooks = self._codebooks
        book = codebooks.get_by_version(project.id, codebook_version)
        run = CodingRun(
            project_id=project.id,
            pack=pack.id,
            codebook_version=book.version,
            model=self._provider.model_id,
            temperature=self._config.llm.temperature,
            seed=self._config.llm.seed,
            status="running",
        )
        self._session.add(run)
        self._session.commit()
        self._session.refresh(run)
        self._audit.emit(
            "system",
            "run.started",
            {
                "run_id": run.id,
                "pack": pack.id,
                "codebook_version": book.version,
                "model": run.model,
                "temperature": run.temperature,
                "seed": run.seed,
            },
        )

        stats: dict[str, Any] = {
            "segments_coded": 0,
            "assignments": 0,
            "flagged": 0,
            "retries": 0,
            "tokens": 0,
            "batches": 0,
            "suggestions": 0,
            "errors": 0,
        }
        try:
            outcomes = self._run_batches(run, project, pack, book, batch_size, document_id)
            for outcome in outcomes:
                stats["batches"] += 1
                stats["assignments"] += outcome.assignments_created
                stats["suggestions"] += outcome.suggestions_added
                stats["retries"] += outcome.retries
                stats["tokens"] += outcome.tokens
                if outcome.flagged:
                    stats["flagged"] += len(outcome.segment_ids)
                if outcome.error:
                    stats["errors"] += 1
                stats["segments_coded"] += 0 if outcome.flagged else len(outcome.segment_ids)
            run.status = "completed"
        except Exception as exc:
            run.status = "failed"
            stats["fatal_error"] = str(exc)
            self._audit.emit("system", "run.failed", {"run_id": run.id, "error": str(exc)})
            raise
        finally:
            run.finished_at = utcnow_naive()
            run.stats_json = json.dumps(stats)
            self._session.add(run)
            self._session.commit()
            self._session.refresh(run)
            if run.status == "completed":
                self._audit.emit("system", "run.completed", {"run_id": run.id, **stats})
        return run

    def get_run(self, run_id: str) -> CodingRun:
        """Fetch a coding run by id."""
        run = self._session.get(CodingRun, run_id)
        if run is None:
            raise ValidationError(f"Run {run_id} not found")
        return run

    def run_stats(self, run: CodingRun) -> dict[str, Any]:
        """Decode a run's stats JSON."""
        return dict(json.loads(run.stats_json or "{}"))

    # -- internals -----------------------------------------------------------

    def _run_batches(
        self,
        run: CodingRun,
        project: Project,
        pack: Pack,
        book: Codebook,
        batch_size: int | None,
        document_id: str | None,
    ) -> list[BatchOutcome]:
        segments = self._load_segments(project.id, document_id)
        if not segments:
            raise ValidationError("No segments to code; import and segment first")
        batches = self._batch_segments(
            segments,
            batch_size if batch_size is not None else self._config.coding.batch_size,
            self._config.coding.batch_token_budget,
        )
        engine = PackEngine(pack)
        assert self._codebooks is not None  # guarded in start_run
        codes = self._codebooks.codes(book)
        system_prompt = engine.render_system_prompt(codes)
        outcomes: list[BatchOutcome] = []
        for index, batch in enumerate(batches):
            outcome = self._code_batch(run, engine, book, system_prompt, index, batch)
            outcomes.append(outcome)
            self._audit.emit(
                "system",
                "run.batch_completed",
                {
                    "run_id": run.id,
                    "batch_index": index,
                    "segments": len(batch),
                    "assignments": outcome.assignments_created,
                    "flagged": outcome.flagged,
                    "error": outcome.error,
                },
            )
        return outcomes

    def _load_segments(self, project_id: str, document_id: str | None) -> list[Segment]:
        stmt = (
            select(Segment)
            .join(Document, col(Segment.document_id) == col(Document.id))
            .where(col(Document.project_id) == project_id)
            .order_by(col(Document.imported_at), col(Segment.document_id), col(Segment.index))
        )
        if document_id is not None:
            if self._session.get(Document, document_id) is None:
                raise DocumentNotFound(f"Document {document_id} not found")
            stmt = stmt.where(col(Segment.document_id) == document_id)
        return list(self._session.exec(stmt))

    @staticmethod
    def _batch_segments(
        segments: list[Segment], max_count: int, token_budget: int
    ) -> list[list[Segment]]:
        """Group segments into batches capped by count and estimated tokens."""
        batches: list[list[Segment]] = []
        current: list[Segment] = []
        current_tokens = 0
        for seg in segments:
            # Length estimate only (ADR-002): offsets are exact, so this rough
            # size suffices for batching decisions.
            seg_tokens = max(1, (seg.end_offset - seg.start_offset) // CHARS_PER_TOKEN)
            if current and (
                len(current) >= max_count or current_tokens + seg_tokens > token_budget
            ):
                batches.append(current)
                current = []
                current_tokens = 0
            current.append(seg)
            current_tokens += seg_tokens
        if current:
            batches.append(current)
        return batches

    def _code_batch(
        self,
        run: CodingRun,
        engine: PackEngine,
        book: Codebook,
        system_prompt: str,
        batch_index: int,
        batch: list[Segment],
    ) -> BatchOutcome:
        outcome = BatchOutcome(batch_index=batch_index, segment_ids=[s.id for s in batch])
        doc_texts = {
            doc.id: doc.raw_text
            for doc in self._session.exec(
                select(Document).where(col(Document.id).in_([s.document_id for s in batch]))
            )
        }
        seg_triples = [
            (seg.id, _slice_text(doc_texts[seg.document_id], seg), seg.speaker) for seg in batch
        ]
        memories = (
            self._memory.retrieve(" ".join(text for _, text, _ in seg_triples))
            if self._memory is not None
            else []
        )
        user_prompt = engine.render_user_prompt(seg_triples, memories)
        from qualagent.llm.base import Message

        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=user_prompt),
        ]
        try:
            result, attempts = structured_call(
                self._provider,
                messages,
                output_model=CodingBatchResult,
                temperature=self._config.llm.temperature,
                seed=self._config.llm.seed,
                audit=self._audit,
            )
        except LLMValidationFailed as exc:
            outcome.flagged = True
            outcome.error = str(exc)
            outcome.retries = 2
            return outcome

        outcome.retries = max(0, len(attempts) - 1)
        outcome.tokens = sum(a.get("tokens", 0) for a in attempts)
        code_map = self._resolve_codes(book, result)
        for seg_result in result.results:
            for asg in seg_result.assignments:
                code = code_map.get(asg.code)
                if code is None:
                    # Unknown code name: treat as a suggestion instead of dropping.
                    continue
                self._session.add(
                    Assignment(
                        segment_id=seg_result.segment_id,
                        code_id=code.id,
                        run_id=run.id,
                        source="ai",
                        rationale=asg.rationale,
                        confidence=asg.confidence,
                        status="pending",
                    )
                )
                outcome.assignments_created += 1
        self._session.commit()

        suggestions = [
            CodeSuggestion(
                name=p.name,
                definition=p.definition,
                example_segment_id=p.example_segment_id,
            )
            for p in result.new_code_proposals
        ]
        if suggestions:
            assert self._codebooks is not None  # guarded in start_run
            outcome.suggestions_added = self._codebooks.add_suggestions(book, suggestions)
        return outcome

    def _resolve_codes(self, book: Codebook, result: CodingBatchResult) -> dict[str, Code]:
        """Map returned code names to Code rows.

        Codes used by assignments but missing from the codebook are created as
        draft codes: initial coding (Braun & Clarke phase 2 / open coding)
        produces the codebook on first pass. Explicit ``new_code_proposals``
        remain suggestions via :meth:`add_suggestions` — never auto-applied.
        """
        assert self._codebooks is not None  # guarded in start_run
        code_map = self._codebooks.code_map(book)
        used_names = {a.code for r in result.results for a in r.assignments}
        for name in sorted(used_names - set(code_map)):
            code = self._codebooks.create_draft_code(book, name=name)
            code_map[name] = code
        return code_map


def _slice_text(raw_text: str, seg: Segment) -> str:
    return raw_text[seg.start_offset : seg.end_offset]
