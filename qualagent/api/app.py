"""REST API (FastAPI) per 03_API_SPEC section 3.

Thin routers over the service layer. A shared exception handler maps every
QualAgentError to the structured error object with the 9 shared error codes.
Local-first: the server opens studies from the project registry on demand.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, select

from qualagent import __version__
from qualagent.config import global_config_dir
from qualagent.domain.errors import QualAgentError
from qualagent.domain.schemas import (
    AssignmentOut,
    CodebookOut,
    CodeIn,
    CodeOut,
    DecisionIn,
    DocumentOut,
    Page,
    ProjectIn,
    ProjectOut,
    RunIn,
    RunOut,
    SegmentOut,
)
from qualagent.runtime import ProjectContext, open_project
from qualagent.services.audit_service import AuditService
from qualagent.services.project_service import ProjectService
from qualagent.services.segmentation_service import Strategy
from qualagent.storage.registry import ProjectRegistry

app = FastAPI(
    title="QualAgent API",
    version=__version__,
    description="Methodology-faithful AI agent for qualitative data analysis.",
)

#: Domain error code -> HTTP status (03_API_SPEC section 2).
_ERROR_STATUS = {
    "PROJECT_NOT_FOUND": 404,
    "DOCUMENT_NOT_FOUND": 404,
    "SEGMENT_NOT_FOUND": 404,
    "CODEBOOK_LOCKED": 409,
    "INVALID_PACK": 422,
    "LLM_VALIDATION_FAILED": 502,
    "LLM_PROVIDER_ERROR": 502,
    "REMOTE_PROVIDER_NOT_ACCEPTED": 403,
    "CONFLICT": 409,
    "VALIDATION_ERROR": 422,
}


@app.exception_handler(QualAgentError)
async def qualagent_error_handler(_request: Any, exc: QualAgentError) -> JSONResponse:
    """Map domain errors to the shared error object."""
    status = _ERROR_STATUS.get(exc.error_code, 500)
    return JSONResponse(
        status_code=status,
        content={"error": {"code": exc.error_code, "message": str(exc)}},
    )


def _projects_service() -> ProjectService:
    registry = ProjectRegistry(global_config_dir() / "registry.json")
    return ProjectService(registry)


def _ctx(project_id: str) -> ProjectContext:
    svc = _projects_service()
    return svc.open(project_id)


ProjectCtx = Annotated[ProjectContext, Depends(_ctx)]


# -- projects ----------------------------------------------------------------


@app.post("/api/v1/projects", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectIn, study_root: Path | None = None) -> Any:
    svc = _projects_service()
    root = study_root or Path.cwd()
    pctx = svc.create(body.name, study_root=root, pack=body.pack)
    project = pctx.project
    created_at, active_pack = project.created_at, project.active_pack
    active_version = project.active_codebook_version
    pctx.engine.dispose()
    return ProjectOut(
        id=project.id,
        name=project.name,
        created_at=created_at,
        active_pack=active_pack,
        active_codebook_version=active_version,
    )


@app.get("/api/v1/projects", response_model=Page)
def list_projects(limit: int = Query(50, ge=1, le=200), offset: int = 0) -> Any:
    from datetime import datetime as _dt

    entries = _projects_service().list_projects()
    window = entries[offset : offset + limit]
    items = [
        ProjectOut(
            id=e.project_id,
            name=e.name,
            created_at=_dt.fromisoformat(e.created_at),
        )
        for e in window
    ]
    return Page(items=items, total=len(entries))


@app.get("/api/v1/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str) -> Any:
    row = _projects_service().get_project_row(project_id)
    return ProjectOut.model_validate(
        {
            "id": row.id,
            "name": row.name,
            "created_at": row.created_at,
            "active_pack": row.active_pack,
            "active_codebook_version": row.active_codebook_version,
        }
    )


@app.delete("/api/v1/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    _projects_service().delete(project_id)


# -- documents & segments -----------------------------------------------------


@app.post("/api/v1/projects/{project_id}/documents", response_model=DocumentOut, status_code=201)
def upload_document(
    ctx: ProjectCtx,
    file: UploadFile,
    strategy: Strategy = "sentence",
) -> Any:
    from qualagent.services.ingestion_service import IngestionService

    qualagent_files = _file_store(ctx)
    session = ctx.session()
    audit = AuditService(session, ctx.project.id)
    ingestion = IngestionService(session, qualagent_files, audit, _segmentation(session))
    data = file.file.read()
    tmp = Path(tempfile.gettempdir()) / (file.filename or "upload.bin")
    tmp.write_bytes(data)
    doc, segments = ingestion.ingest_file(ctx.project.id, tmp, strategy=strategy)
    out = _document_out(session, doc, len(segments))
    session.close()
    ctx.engine.dispose()
    return out


@app.get("/api/v1/projects/{project_id}/documents", response_model=Page)
def list_documents(ctx: ProjectCtx, limit: int = Query(50, ge=1), offset: int = 0) -> Any:
    from qualagent.services.ingestion_service import IngestionService

    session = ctx.session()
    audit = AuditService(session, ctx.project.id)
    ingestion = IngestionService(session, _file_store(ctx), audit, _segmentation(session))
    docs, total = ingestion.list_documents(ctx.project.id, limit=limit, offset=offset)
    from qualagent.services.segmentation_service import SegmentationService

    seg_svc = SegmentationService(session)
    items = []
    for d in docs:
        _, seg_total = seg_svc.list_segments(d.id, limit=1)
        items.append(_document_out(session, d, seg_total))
    session.close()
    ctx.engine.dispose()
    return Page(items=items, total=total)


@app.get("/api/v1/documents/{document_id}/segments", response_model=Page)
def list_segments(document_id: str, limit: int = Query(50, ge=1), offset: int = 0) -> Any:
    from qualagent.domain.models import Document
    from qualagent.services.segmentation_service import SegmentationService

    ctx, session = _ctx_for_row(select(Document).where(Document.id == document_id))
    segs, total = SegmentationService(session).list_segments(
        document_id, limit=limit, offset=offset
    )
    doc = session.get(Document, document_id)
    items = [
        SegmentOut(
            id=s.id,
            document_id=s.document_id,
            index=s.index,
            start_offset=s.start_offset,
            end_offset=s.end_offset,
            speaker=s.speaker,
            segmenter_version=s.segmenter_version,
            text=doc.raw_text[s.start_offset : s.end_offset] if doc else "",
        )
        for s in segs
    ]
    session.close()
    ctx.engine.dispose()
    return Page(items=items, total=total)


@app.post("/api/v1/documents/{document_id}/resegment")
def resegment(document_id: str, strategy: Strategy = "sentence") -> Any:
    from qualagent.domain.models import Document
    from qualagent.services.ingestion_service import IngestionService

    ctx, session = _ctx_for_row(select(Document).where(Document.id == document_id))
    audit = AuditService(session, ctx.project.id)
    ingestion = IngestionService(session, _file_store(ctx), audit, _segmentation(session))
    segments = ingestion.resegment(document_id, strategy=strategy)
    count = len(segments)
    session.close()
    ctx.engine.dispose()
    return {"created": count}


def _segmentation(session: Session) -> Any:
    from qualagent.services.segmentation_service import SegmentationService

    return SegmentationService(session)


def _file_store(ctx: ProjectContext) -> Any:
    from qualagent.storage.files import FileStore

    return FileStore(ctx.project_dir)


def _document_out(session: Session, doc: Any, seg_count: int) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        project_id=doc.project_id,
        filename=doc.filename,
        sha256=doc.sha256,
        mime=doc.mime,
        imported_at=doc.imported_at,
        segment_count=seg_count,
    )


# -- coding -------------------------------------------------------------------


@app.post("/api/v1/projects/{project_id}/runs", response_model=RunOut, status_code=201)
def start_run(ctx: ProjectCtx, body: RunIn) -> Any:
    from qualagent.llm.factory import create_provider
    from qualagent.packs.loader import load_pack
    from qualagent.services.codebook_service import CodebookService
    from qualagent.services.coding_service import CodingService
    from qualagent.services.memory_service import MemoryService

    session = ctx.session()
    audit = AuditService(session, ctx.project.id)
    provider = create_provider(ctx.config)
    pack = load_pack(body.pack, project_packs_dir=ctx.project_dir / "packs")
    codebooks = CodebookService(session, audit)
    memory = MemoryService(session, ctx.project.id, ctx.project_dir, ctx.config)
    svc = CodingService(session, audit, codebooks, memory, provider, ctx.config)
    run = svc.start_run(
        ctx.project,
        pack,
        codebook_version=body.codebook_version,
        batch_size=body.batch_size,
    )
    out = _run_out(run)
    session.close()
    ctx.engine.dispose()
    provider.close()
    return out


@app.get("/api/v1/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str) -> Any:
    from qualagent.domain.models import CodingRun

    ctx, session = _ctx_for_row(select(CodingRun).where(CodingRun.id == run_id))
    run = session.get(CodingRun, run_id)
    if run is None:  # pragma: no cover - guarded by _ctx_for_row
        raise HTTPException(status_code=404, detail="run not found")
    out = _run_out(run)
    session.close()
    ctx.engine.dispose()
    return out


def _run_out(run: Any) -> RunOut:
    return RunOut(
        id=run.id,
        project_id=run.project_id,
        pack=run.pack,
        codebook_version=run.codebook_version,
        model=run.model,
        temperature=run.temperature,
        seed=run.seed,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        stats=dict(json.loads(run.stats_json)),
    )


@app.get("/api/v1/projects/{project_id}/assignments", response_model=Page)
def list_assignments(
    ctx: ProjectCtx,
    status: str | None = None,
    code: str | None = None,
    confidence_min: float = 0.0,
    confidence_max: float = 1.0,
    limit: int = Query(50, ge=1),
    offset: int = 0,
) -> Any:
    from qualagent.services.review_service import ReviewService

    session = ctx.session()
    svc = ReviewService(session, AuditService(session, ctx.project.id))
    items, total = svc.list_items(
        status=status,
        code=code,
        min_confidence=confidence_min,
        max_confidence=confidence_max,
        limit=limit,
        offset=offset,
    )
    out = [AssignmentOut.model_validate(i.assignment) for i in items]
    session.close()
    ctx.engine.dispose()
    return Page(items=out, total=total)


@app.post("/api/v1/assignments/{assignment_id}/decision", response_model=AssignmentOut)
def decide(assignment_id: str, body: DecisionIn) -> Any:
    from qualagent.domain.models import Assignment, Code
    from qualagent.services.memory_service import MemoryService
    from qualagent.services.review_service import ReviewService

    ctx, session = _ctx_for_row(select(Assignment).where(Assignment.id == assignment_id))
    audit = AuditService(session, ctx.project.id)
    memory = MemoryService(session, ctx.project.id, ctx.project_dir, ctx.config)
    svc = ReviewService(session, audit, memory)
    if body.action == "approve":
        row = svc.approve(assignment_id, note=body.note or "")
    elif body.action == "reject":
        row = svc.reject(assignment_id, note=body.note or "")
    else:
        if body.code_id is None:
            raise HTTPException(status_code=422, detail="code_id required for edit")
        code = session.get(Code, body.code_id)
        if code is None:
            raise HTTPException(status_code=422, detail="code_id not found")
        row = svc.edit_code(assignment_id, new_code_name=code.name, note=body.note or "")
    out = AssignmentOut.model_validate(row)
    session.close()
    ctx.engine.dispose()
    return out


# -- codebook -----------------------------------------------------------------


@app.get("/api/v1/projects/{project_id}/codebooks", response_model=Page)
def list_codebooks(ctx: ProjectCtx) -> Any:
    from qualagent.services.codebook_service import CodebookService

    session = ctx.session()
    svc = CodebookService(session, AuditService(session, ctx.project.id))
    books = svc.list_codebooks(ctx.project.id)
    items = [
        CodebookOut(
            id=b.id,
            project_id=b.project_id,
            version=b.version,
            status=b.status,
            locked_at=b.locked_at,
        )
        for b in books
    ]
    session.close()
    ctx.engine.dispose()
    return Page(items=items, total=len(items))


@app.get("/api/v1/codebooks/{codebook_id}", response_model=CodebookOut)
def get_codebook(codebook_id: str) -> Any:
    from qualagent.domain.models import Code, Codebook

    ctx, session = _ctx_for_row(select(Codebook).where(Codebook.id == codebook_id))
    book = session.get(Codebook, codebook_id)
    if book is None:
        session.close()
        ctx.engine.dispose()
        raise HTTPException(status_code=404, detail="codebook not found")
    codes = list(session.exec(select(Code).where(Code.codebook_id == codebook_id)))
    out = CodebookOut(
        id=book.id,
        project_id=book.project_id,
        version=book.version,
        status=book.status,
        locked_at=book.locked_at,
        codes=[_code_out(c) for c in codes],
    )
    session.close()
    ctx.engine.dispose()
    return out


def _code_out(c: Any) -> CodeOut:
    """Code rows store example ids as a JSON string; expand for the schema."""
    return CodeOut(
        id=c.id,
        codebook_id=c.codebook_id,
        parent_id=c.parent_id,
        name=c.name,
        definition=c.definition,
        inclusion_criteria=c.inclusion_criteria,
        exclusion_criteria=c.exclusion_criteria,
        example_segment_ids=json.loads(c.example_segment_ids or "[]"),
    )


@app.post("/api/v1/codebooks/{codebook_id}/refine", response_model=CodebookOut)
def refine_codebook(codebook_id: str, from_suggestions: bool = False) -> Any:
    from qualagent.services.codebook_service import CodebookService

    ctx, session = _ctx_for_codebook(codebook_id)
    svc = CodebookService(session, AuditService(session, ctx.project.id))
    book = svc.refine(ctx.project, from_suggestions=from_suggestions)
    out = _codebook_out(svc, book)
    session.close()
    ctx.engine.dispose()
    return out


@app.post("/api/v1/codebooks/{codebook_id}/lock", response_model=CodebookOut)
def lock_codebook(codebook_id: str) -> Any:
    from qualagent.services.codebook_service import CodebookService

    ctx, session = _ctx_for_codebook(codebook_id)
    svc = CodebookService(session, AuditService(session, ctx.project.id))
    book = svc.lock(ctx.project)
    out = _codebook_out(svc, book)
    session.close()
    ctx.engine.dispose()
    return out


@app.post("/api/v1/codebooks/{codebook_id}/codes", response_model=CodeOut, status_code=201)
def add_code(codebook_id: str, body: CodeIn) -> Any:
    from qualagent.services.codebook_service import CodebookService

    ctx, session = _ctx_for_codebook(codebook_id)
    svc = CodebookService(session, AuditService(session, ctx.project.id))
    book = svc.get(codebook_id)
    code = svc.add_code(
        book,
        name=body.name,
        definition=body.definition,
        inclusion_criteria=body.inclusion_criteria,
        exclusion_criteria=body.exclusion_criteria,
        parent_id=body.parent_id,
        example_segment_ids=body.example_segment_ids,
    )
    out = _code_out(code)
    session.close()
    ctx.engine.dispose()
    return out


def _ctx_for_codebook(codebook_id: str) -> tuple[ProjectContext, Session]:
    from qualagent.domain.models import Codebook

    return _ctx_for_row(select(Codebook).where(Codebook.id == codebook_id))


def _codebook_out(svc: Any, book: Any) -> CodebookOut:
    return CodebookOut(
        id=book.id,
        project_id=book.project_id,
        version=book.version,
        status=book.status,
        locked_at=book.locked_at,
        codes=[_code_out(c) for c in svc.codes(book)],
    )


# -- analysis & exports ---------------------------------------------------------


@app.get("/api/v1/projects/{project_id}/irr")
def compute_irr(ctx: ProjectCtx) -> Any:
    from qualagent.services.irr_service import IRRCalculator

    session = ctx.session()
    report = IRRCalculator(session).compute()
    session.close()
    ctx.engine.dispose()
    return {
        "overall_kappa": report.overall_kappa,
        "per_code": report.per_code,
        "n_compared": report.n_compared,
    }


@app.post("/api/v1/projects/{project_id}/exports")
def run_export(ctx: ProjectCtx, kind: str, format: str, out: Path | None = None) -> Any:
    from qualagent.services.export_service import ExportService

    session = ctx.session()
    svc = ExportService(session, AuditService(session, ctx.project.id))
    target = out or Path(tempfile.gettempdir()) / f"qualagent_{kind}.{format}"
    if kind == "matrix":
        exported = svc.export_matrix(target, fmt=format)
    elif kind == "codebook":
        exported = svc.export_codebook(target, fmt=format)
    elif kind == "audit":
        exported = svc.export_audit(target, fmt=format)
    elif kind == "methods":
        exported = svc.export_methods(target)
    else:
        session.close()
        ctx.engine.dispose()
        raise HTTPException(status_code=422, detail=f"unknown export kind {kind}")
    session.close()
    ctx.engine.dispose()
    return FileResponse(exported.path, filename=exported.path.name)


@app.get("/api/v1/projects/{project_id}/audit", response_model=Page)
def list_audit_events(
    ctx: ProjectCtx,
    event_type: str | None = None,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = 0,
) -> Any:
    session = ctx.session()
    audit = AuditService(session, ctx.project.id)
    events, total = audit.list_events(event_type=event_type, limit=limit, offset=offset)
    payload = [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "actor": e.actor,
            "event_type": e.event_type,
            "payload": AuditService.payload_of(e),
        }
        for e in events
    ]
    session.close()
    ctx.engine.dispose()
    return Page(items=payload, total=total)


def _ctx_for_row(stmt: Any) -> tuple[ProjectContext, Session]:
    """Scan registry projects for a row matching the select statement."""
    registry = ProjectRegistry(global_config_dir() / "registry.json")
    for entry in registry.list_all():
        try:
            ctx = open_project(Path(entry.path) / ".qualagent")
        except QualAgentError:
            continue
        with ctx.session() as session:
            found = session.exec(stmt).first()
        if found is not None:
            return ctx, ctx.session()
        ctx.engine.dispose()
    raise HTTPException(status_code=404, detail="not found")
