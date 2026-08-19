"""Exports: code matrix, codebook, audit trail, methods paragraph (FR-10).

All export content is derived from the auditable database rows — never from
prompts or LLM output directly — so every exported quote can be traced to
exact source offsets in the original document (the fidelity guarantee).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, col, select

from qualagent.domain.models import (
    Assignment,
    AuditEvent,
    Code,
    Codebook,
    CodingRun,
    Document,
    Project,
    Segment,
)
from qualagent.services.audit_service import AuditService
from qualagent.services.irr_service import IRRCalculator


@dataclass
class ExportedFile:
    """A completed export with its target path."""

    path: Path
    kind: str  # matrix | codebook | audit | methods
    format: str  # csv | xlsx | md | pdf | jsonl


class ExportService:
    """Render project artifacts to disk in publication-ready formats."""

    def __init__(self, session: Session, audit: AuditService) -> None:
        self._session = session
        self._audit = audit

    # -- matrix ---------------------------------------------------------------

    def export_matrix(self, out: Path, *, fmt: str = "csv") -> ExportedFile:
        """Code x document frequency matrix (approved + pending assignments)."""
        rows = self._matrix_rows()
        if fmt == "xlsx":
            self._matrix_xlsx(out, rows)
        else:
            self._matrix_csv(out, rows)
        return ExportedFile(path=out, kind="matrix", format=fmt)

    def _matrix_rows(self) -> list[dict[str, object]]:
        project = self._project()
        book = self._locked_or_latest_codebook(project)
        codes = list(
            self._session.exec(
                select(Code).where(col(Code.codebook_id) == book.id).order_by(col(Code.name))
            ).all()
        )
        docs = list(
            self._session.exec(
                select(Document)
                .where(col(Document.project_id) == project.id)
                .order_by(col(Document.imported_at))
            ).all()
        )
        doc_names = {d.id: d.filename for d in docs}
        counts: dict[tuple[str, str], int] = {}
        for a in self._counted_assignments():
            seg = self._session.get(Segment, a.segment_id)
            if seg is None:
                continue
            counts[(a.code_id, seg.document_id)] = counts.get((a.code_id, seg.document_id), 0) + 1
        parents = {c.id: self._parent_name(c, codes) for c in codes}
        rows: list[dict[str, object]] = []
        for c in codes:
            parent = parents[c.id]
            flat = f"{parent}.{c.name}" if parent else c.name
            row: dict[str, object] = {"code": flat, "definition": c.definition}
            for d in docs:
                row[doc_names[d.id]] = counts.get((c.id, d.id), 0)
            rows.append(row)
        return rows

    @staticmethod
    def _parent_name(code: Code, codes: list[Code]) -> str | None:
        if code.parent_id is None:
            return None
        for c in codes:
            if c.id == code.parent_id:
                return c.name
        return None

    def _counted_assignments(self) -> list[Assignment]:
        return list(
            self._session.exec(
                select(Assignment).where(col(Assignment.status).in_(["approved", "pending"]))
            ).all()
        )

    def _matrix_csv(self, out: Path, rows: list[dict[str, object]]) -> None:
        import csv

        out.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            out.write_text("", encoding="utf-8")
            return
        headers = list(rows[0].keys())
        with out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def _matrix_xlsx(self, out: Path, rows: list[dict[str, object]]) -> None:
        import pandas as pd

        out.parent.mkdir(parents=True, exist_ok=True)
        detail = self._segment_detail_frame()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            pd.DataFrame(rows).to_excel(writer, sheet_name="matrix", index=False)
            detail.to_excel(writer, sheet_name="segments", index=False)

    def _segment_detail_frame(self) -> Any:
        import pandas as pd

        records = []
        for a in self._counted_assignments():
            seg = self._session.get(Segment, a.segment_id)
            if seg is None:
                continue
            doc = self._session.get(Document, seg.document_id)
            code = self._session.get(Code, a.code_id)
            records.append(
                {
                    "segment_id": a.segment_id,
                    "document": doc.filename if doc else "?",
                    "segment_index": seg.index,
                    "start_offset": seg.start_offset,
                    "end_offset": seg.end_offset,
                    "code": code.name if code else "?",
                    "status": a.status,
                    "confidence": a.confidence,
                    "rationale": a.rationale,
                }
            )
        return pd.DataFrame(records)

    # -- codebook ---------------------------------------------------------------

    def export_codebook(self, out: Path, *, fmt: str = "md") -> ExportedFile:
        """Codebook document with definitions, criteria, and example quotes."""
        content = self._codebook_markdown()
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "pdf":
            self._pdf_from_markdown(out, content)
        else:
            out.write_text(content, encoding="utf-8")
        return ExportedFile(path=out, kind="codebook", format=fmt)

    def _codebook_markdown(self) -> str:
        project = self._project()
        book = self._locked_or_latest_codebook(project)
        codes = list(
            self._session.exec(
                select(Code).where(col(Code.codebook_id) == book.id).order_by(col(Code.name))
            ).all()
        )
        lines = [f"# Codebook — {project.name} (v{book.version}, {book.status})", ""]
        for c in codes:
            lines.append(f"## {c.name}")
            lines.append("")
            if c.definition:
                lines.append(f"**Definition.** {c.definition}")
            if c.inclusion_criteria:
                lines.append(f"**Include when.** {c.inclusion_criteria}")
            if c.exclusion_criteria:
                lines.append(f"**Exclude when.** {c.exclusion_criteria}")
            quotes = self._example_quotes(c, limit=2)
            if quotes:
                lines.append("**Example quotes.**")
                for text, doc_name, start, end in quotes:
                    lines.append(f"> {text}")
                    lines.append(f"— {doc_name} [{start}:{end}]")
            lines.append("")
        return "\n".join(lines)

    def _example_quotes(self, code: Code, *, limit: int) -> list[tuple[str, str, int, int]]:
        """Approved assignments for this code, rendered with source offsets."""
        quotes: list[tuple[str, str, int, int]] = []
        example_ids = json.loads(code.example_segment_ids or "[]")
        seg_filter = example_ids if example_ids else None
        assignments = list(
            self._session.exec(
                select(Assignment)
                .where(col(Assignment.code_id) == code.id)
                .where(col(Assignment.status) == "approved")
            ).all()
        )
        for a in assignments:
            if seg_filter is not None and a.segment_id not in seg_filter:
                continue
            seg = self._session.get(Segment, a.segment_id)
            if seg is None:
                continue
            doc = self._session.get(Document, seg.document_id)
            if doc is None:
                continue
            text = doc.raw_text[seg.start_offset : seg.end_offset].replace("\n", " ")
            quotes.append((text, doc.filename, seg.start_offset, seg.end_offset))
            if len(quotes) >= limit:
                break
        return quotes

    # -- audit ----------------------------------------------------------------

    def export_audit(self, out: Path, *, fmt: str = "jsonl") -> ExportedFile:
        """Full append-only audit trail as JSONL, or PDF grouped by run."""
        out.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "pdf":
            self._pdf_from_markdown(out, self._audit_markdown())
        else:
            events, _total = self._audit.list_events(limit=10**9)
            with out.open("w", encoding="utf-8") as f:
                for e in events:
                    f.write(
                        json.dumps(
                            {
                                "id": e.id,
                                "timestamp": e.ts.isoformat(),
                                "actor": e.actor,
                                "event_type": e.event_type,
                                "payload": AuditService.payload_of(e),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        return ExportedFile(path=out, kind="audit", format=fmt)

    def _audit_markdown(self) -> str:
        events, _total = self._audit.list_events(limit=10**9)
        lines = ["# Audit trail", ""]
        for e in events:
            payload = json.dumps(AuditService.payload_of(e), ensure_ascii=False)
            lines.append(f"- **{e.ts.isoformat()}** `{e.actor}` `{e.event_type}` {payload}")
        return "\n".join(lines)

    # -- methods paragraph ---------------------------------------------------

    def export_methods(self, out: Path) -> ExportedFile:
        """Draft methods-section paragraph (MD) per 04_DATA_MODEL §5."""
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self._methods_paragraph(), encoding="utf-8")
        return ExportedFile(path=out, kind="methods", format="md")

    def _methods_paragraph(self) -> str:
        project = self._project()
        runs = list(
            self._session.exec(
                select(CodingRun)
                .where(col(CodingRun.project_id) == project.id)
                .order_by(col(CodingRun.started_at))
            ).all()
        )
        completed = [r for r in runs if r.status == "completed"]
        segments_coded = sum(json.loads(r.stats_json).get("segments_coded", 0) for r in completed)
        documents = len(
            list(
                self._session.exec(
                    select(Document).where(col(Document.project_id) == project.id)
                ).all()
            )
        )
        decisions = list(
            self._session.exec(select(Assignment).where(col(Assignment.status) != "pending")).all()
        )
        irr = IRRCalculator(self._session).compute()
        model = completed[0].model if completed else "n/a"
        temperature = completed[0].temperature if completed else 0.0
        date_range = (
            f"{completed[0].started_at.date().isoformat()} to "
            f"{(completed[-1].finished_at or datetime.now()).date().isoformat()}"
            if completed
            else "n/a"
        )
        pack_ids = sorted({r.pack for r in completed})
        citations = self._pack_citations(pack_ids)
        kappa = f"{irr.overall_kappa:.2f}" if irr.n_compared else "not computed"
        return (
            f"# Methods\n\n"
            f"Data were analyzed with QualAgent, a local-first AI-assisted "
            f"qualitative coding tool. {documents} document(s) were imported and "
            f"segmented; {segments_coded} segment(s) were coded using the "
            f"{', '.join(pack_ids) or 'n/a'} methodology pack(s) "
            f"({citations or 'citation in pack definition'}). Coding was "
            f"performed by {model} at temperature {temperature} in "
            f"deterministic batches. All AI assignments were stored as pending "
            f"and required explicit human approval: {len(decisions)} decision(s) "
            f"were recorded in the append-only audit trail. Inter-rater "
            f"reliability between AI and human decisions (Cohen's kappa) was "
            f"{kappa} over {irr.n_compared} compared segment(s). The analysis "
            f"period was {date_range}. A complete, tamper-evident audit log of "
            f"every AI call and human decision is available from the project "
            f"export.\n"
        )

    def _pack_citations(self, pack_ids: list[str]) -> str:
        from qualagent.packs.loader import load_pack

        parts: list[str] = []
        for pid in pack_ids:
            try:
                pack = load_pack(pid)
                parts.append(pack.citation)
            except Exception:
                continue
        return "; ".join(parts)

    # -- shared internals ------------------------------------------------------

    def _project(self) -> Project:
        from qualagent.domain.errors import ValidationError

        projects = self._session.exec(select(Project)).all()
        if not projects:
            raise ValidationError("No project found")
        return projects[0]

    def _locked_or_latest_codebook(self, project: Project) -> Codebook:
        books = list(
            self._session.exec(
                select(Codebook)
                .where(col(Codebook.project_id) == project.id)
                .order_by(col(Codebook.version))
            ).all()
        )
        if not books:
            from qualagent.domain.errors import ValidationError

            raise ValidationError("No codebook exists")
        locked = [b for b in books if b.status == "locked"]
        return locked[-1] if locked else books[-1]

    def _pdf_from_markdown(self, out: Path, markdown: str) -> None:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer

        styles = getSampleStyleSheet()
        story: list[Flowable] = []
        for line in markdown.splitlines():
            if not line.strip():
                story.append(Spacer(1, 6))
                continue
            if line.startswith("# "):
                story.append(Paragraph(line[2:], styles["Title"]))
            elif line.startswith("## "):
                story.append(Paragraph(line[3:], styles["Heading2"]))
            elif line.startswith("> "):
                story.append(Paragraph(f"<i>{line[2:]}</i>", styles["BodyText"]))
            else:
                story.append(Paragraph(line, styles["BodyText"]))
        doc = SimpleDocTemplate(str(out), pagesize=A4)
        doc.build(story)


def audit_events_jsonl(session: Session) -> str:
    """Render every AuditEvent in the session as JSONL (used by tests)."""
    events = list(session.exec(select(AuditEvent)).all())
    lines = []
    for e in events:
        lines.append(
            json.dumps(
                {
                    "id": e.id,
                    "timestamp": e.ts.isoformat(),
                    "actor": e.actor,
                    "event_type": e.event_type,
                },
                ensure_ascii=False,
            )
        )
    return "\n".join(lines)
