"""QualAgent MCP server (03_API_SPEC section 4).

Thin tool/resource handlers over the service layer — the same application
services the CLI and REST API use — exposed as Model Context Protocol tools.
Every domain ``QualAgentError`` is mapped to an MCP protocol error whose
``data.code`` carries the shared string error code (03_API_SPEC section 1), so
error semantics are identical across all three interfaces.

Transports: stdio (default), SSE. The server is a subprocess the host spawns
and talks to over stdin/stdout; it opens studies from the machine-level project
registry on demand, exactly like the REST API.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from sqlmodel import Session

from qualagent.config import global_config_dir
from qualagent.domain.errors import ProjectNotFound, QualAgentError, ValidationError
from qualagent.domain.models import Assignment, AuditEvent, Code
from qualagent.domain.schemas import AssignmentOut
from qualagent.llm.factory import create_provider
from qualagent.packs.loader import load_pack
from qualagent.runtime import ProjectContext, open_project
from qualagent.services.audit_service import AuditService
from qualagent.services.codebook_service import CodebookService
from qualagent.services.coding_service import CodingService
from qualagent.services.irr_service import IRRCalculator
from qualagent.services.memory_service import MemoryService
from qualagent.services.review_service import ReviewService
from qualagent.services.search_service import SegmentSearchService
from qualagent.storage.registry import ProjectRegistry

#: Domain error code -> (JSON-RPC code, default message). The JSON-RPC code is
#: in the -32000..-32099 ``Server error`` range or a standard code; the shared
#: QualAgent string code always travels in ``data.code`` (03_API_SPEC section 1).
_ERROR_CODE_TO_MCP: dict[str, tuple[int, str]] = {
    "PROJECT_NOT_FOUND": (-32001, "Project not found"),
    "DOCUMENT_NOT_FOUND": (-32001, "Document not found"),
    "SEGMENT_NOT_FOUND": (-32001, "Segment not found"),
    "CONFLICT": (-32002, "Conflict"),
    "CODEBOOK_LOCKED": (-32002, "Codebook locked"),
    "VALIDATION_ERROR": (-32602, "Invalid request"),
    "INVALID_PACK": (-32602, "Invalid pack"),
    "REMOTE_PROVIDER_NOT_ACCEPTED": (-32003, "Remote provider not accepted"),
    "LLM_VALIDATION_FAILED": (-32004, "LLM validation failed"),
    "LLM_PROVIDER_ERROR": (-32004, "LLM provider error"),
}


def _to_mcp_error(exc: QualAgentError) -> MCPError:
    """Map a domain error to an MCP protocol error carrying the shared code."""
    rpc_code, default_msg = _ERROR_CODE_TO_MCP.get(exc.error_code, (-32603, "Internal error"))
    return MCPError(code=rpc_code, message=str(exc) or default_msg, data={"code": exc.error_code})


def _map_errors(fn: Any) -> Any:
    """Decorate a tool/resource so QualAgentError surfaces as an MCPError.

    Raising an ``MCPError`` from the tool body makes the kernel emit a
    top-level JSON-RPC error (not a ``CallToolResult(isError=True)``), so the
    shared string code reaches the client verbatim.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except QualAgentError as exc:
            raise _to_mcp_error(exc) from exc

    return wrapper


def _qualagent_version() -> str:
    from qualagent import __version__

    return str(__version__)


mcp_server = MCPServer(
    "qualagent",
    title="QualAgent",
    description=(
        "Methodology-faithful AI agent for qualitative data analysis. Tools for "
        "projects, codebooks, coding, review, audit, IRR, and code proposals."
    ),
    version=_qualagent_version(),
)


# -- project opening helpers --------------------------------------------------


def _registry() -> ProjectRegistry:
    return ProjectRegistry(global_config_dir() / "registry.json")


@contextmanager
def _using_project(project_id: str) -> Any:
    """Open a study by id; dispose its engine when the block exits."""
    from qualagent.services.project_service import ProjectService

    ctx = ProjectService(_registry()).open(project_id)
    try:
        yield ctx
    finally:
        ctx.engine.dispose()


def _ctx_for_row(stmt_matcher: Any) -> tuple[ProjectContext, Session]:
    """Scan registered projects for one whose DB satisfies ``stmt_matcher``.

    ``stmt_matcher`` is a callable(session)->row-or-None. Returns the owning
    project context plus a fresh session. Raises ProjectNotFound if no study
    contains the entity (mapped to an MCP error by callers under ``_map_errors``).
    """
    for entry in _registry().list_all():
        try:
            ctx = open_project(Path(entry.path) / ".qualagent")
        except QualAgentError:
            continue
        with ctx.session() as scan:
            found = stmt_matcher(scan)
        if found is not None:
            return ctx, ctx.session()
        ctx.engine.dispose()
    raise ProjectNotFound("Entity not found in any registered project")


def _ctx_for_assignment(assignment_id: str) -> tuple[ProjectContext, Session]:
    return _ctx_for_row(lambda s: s.get(Assignment, assignment_id))


def _make_provider(config: Any) -> Any:
    """Build the configured LLM provider, honoring a fake-cassette test seam.

    When ``QUALAGENT_FAKE_CASSETTE`` points to a YAML cassette (mirroring the
    CLI's ``--llm fake --cassette``), a FakeProvider is used so the server runs
    fully offline. Otherwise the project config's provider is built behind the
    privacy gate (remote providers require ``[llm.remote] accepted = true``).
    """
    fake_cassette = os.environ.get("QUALAGENT_FAKE_CASSETTE")
    if fake_cassette:
        from qualagent.llm.fake import FakeProvider

        return FakeProvider(cassette_path=Path(fake_cassette))
    return create_provider(config)


class _HashEmbedder:
    """Deterministic bag-of-words embedder for offline contract tests.

    Stable across processes (hashlib, not ``hash``), so the MCP subprocess and
    the test process agree on ranking without loading the ONNX MiniLM model.
    """

    dim = 64

    def __call__(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]+", text.lower()):
                h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16) % self.dim
                vec[h] += 1.0
            out.append(vec)
        return out


def _search_embedder() -> Any:
    if os.environ.get("QUALAGENT_FAKE_EMBEDDINGS"):
        return _HashEmbedder()
    from qualagent.services.search_service import default_embedder

    return default_embedder()


def _code_dict(c: Code) -> dict[str, Any]:
    return {
        "id": c.id,
        "codebook_id": c.codebook_id,
        "parent_id": c.parent_id,
        "name": c.name,
        "definition": c.definition,
        "inclusion_criteria": c.inclusion_criteria,
        "exclusion_criteria": c.exclusion_criteria,
        "example_segment_ids": json.loads(c.example_segment_ids or "[]"),
    }


def _event_dict(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "ts": e.ts.isoformat(),
        "actor": e.actor,
        "event_type": e.event_type,
        "payload": AuditService.payload_of(e),
    }


def _resolve_pack_name(ctx: ProjectContext, pack: str | None) -> str:
    return (pack or ctx.project.active_pack or "open_coding").split("@")[0]


# =============================================================================
# Tools (03_API_SPEC section 4 table)
# =============================================================================


@mcp_server.tool(description="List all registered QualAgent projects.")
@_map_errors
def list_projects() -> list[dict[str, Any]]:
    """Return every project in the machine-level registry."""
    from qualagent.services.project_service import ProjectService

    svc = ProjectService(_registry())
    return [
        {"id": e.project_id, "name": e.name, "created_at": e.created_at}
        for e in svc.list_projects()
    ]


@mcp_server.tool(description="Get a project's codebook tree (current or a pinned version).")
@_map_errors
def get_codebook(project_id: str, version: int | None = None) -> dict[str, Any]:
    """Return the current (or specified) codebook with its full code tree.

    A project with no codebook yet returns an empty tree (status ``empty``)
    rather than an error, so a client can query any registered project.
    """
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            svc = CodebookService(session, AuditService(session, ctx.project.id))
            if version is None:
                books = svc.list_codebooks(ctx.project.id)
                if not books:
                    return {
                        "project_id": ctx.project.id,
                        "version": None,
                        "status": "empty",
                        "codes": [],
                    }
                book = books[-1]
            else:
                book = svc.get_by_version(ctx.project.id, version)
            return {
                "id": book.id,
                "project_id": book.project_id,
                "version": book.version,
                "status": book.status,
                "locked_at": book.locked_at.isoformat() if book.locked_at else None,
                "codes": [_code_dict(c) for c in svc.codes(book)],
            }
        finally:
            session.close()


@mcp_server.tool(
    description=(
        "Code one ad-hoc text snippet against the project's active codebook. "
        "Nothing is persisted as a document; the call is audited."
    )
)
@_map_errors
def code_segment(
    project_id: str,
    text: str,
    speaker: str | None = None,
    pack: str | None = None,
) -> dict[str, Any]:
    """Ad-hoc coding of a single snippet (03_API_SPEC section 4 ``code_segment``)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            provider = _make_provider(ctx.config)
            try:
                loaded_pack = load_pack(
                    _resolve_pack_name(ctx, pack), project_packs_dir=ctx.project_dir / "packs"
                )
                books = CodebookService(session, AuditService(session, ctx.project.id))
                books.ensure_codebook(ctx.project)
                audit = AuditService(session, ctx.project.id)
                svc = CodingService(session, audit, books, None, provider, ctx.config)
                return svc.code_segment(ctx.project, loaded_pack, text=text, speaker=speaker)
            finally:
                provider.close()
        finally:
            session.close()


@mcp_server.tool(description="Semantic search over a project's segments.")
@_map_errors
def search_segments(
    project_id: str,
    query: str,
    k: int = 8,
) -> list[dict[str, Any]]:
    """Rank segments by semantic similarity; return the top-k with scores."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            svc = SegmentSearchService(session, embedder=_search_embedder())
            matches = svc.search(ctx.project.id, query, k=k)
            return [
                {
                    "id": m.segment.id,
                    "document_id": m.segment.document_id,
                    "index": m.segment.index,
                    "start_offset": m.segment.start_offset,
                    "end_offset": m.segment.end_offset,
                    "speaker": m.segment.speaker,
                    "segmenter_version": m.segment.segmenter_version,
                    "text": m.text,
                    "score": m.score,
                }
                for m in matches
            ]
        finally:
            session.close()


@mcp_server.tool(description="List a project's assignments, optionally filtered by status or code.")
@_map_errors
def get_assignments(
    project_id: str,
    status: str | None = None,
    code: str | None = None,
) -> list[dict[str, Any]]:
    """Filtered assignment list (enriched with segment text + code name)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            svc = ReviewService(session, AuditService(session, ctx.project.id))
            items, _total = svc.list_items(status=status, code=code, limit=500)
            out: list[dict[str, Any]] = []
            for it in items:
                row = AssignmentOut.model_validate(it.assignment).model_dump(mode="json")
                row["code_name"] = it.code_name
                row["segment_text"] = it.segment_text
                row["speaker"] = it.speaker
                row["document_id"] = it.document_id
                out.append(row)
            return out
        finally:
            session.close()


@mcp_server.tool(
    description="Record a human decision on one assignment: approve / reject / edit."
)
@_map_errors
def submit_human_decision(
    assignment_id: str,
    action: str,
    code_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Approve, reject, or edit an assignment. Editing writes memory (a correction)."""
    if action not in {"approve", "reject", "edit"}:
        raise ValidationError(f"action must be approve|reject|edit, got {action!r}")
    if action == "edit" and code_id is None:
        raise ValidationError("code_id is required when action is edit")

    ctx, session = _ctx_for_assignment(assignment_id)
    try:
        audit = AuditService(session, ctx.project.id)
        if action == "edit":
            memory = MemoryService(session, ctx.project.id, ctx.project_dir, ctx.config)
            code = session.get(Code, code_id)
            if code is None:
                raise ValidationError(f"code_id {code_id} not found")
            row = ReviewService(session, audit, memory).edit_code(
                assignment_id, new_code_name=code.name, note=note or ""
            )
        elif action == "approve":
            row = ReviewService(session, audit, None).approve(assignment_id, note=note or "")
        else:  # reject
            row = ReviewService(session, audit, None).reject(assignment_id, note=note or "")
        return AssignmentOut.model_validate(row).model_dump(mode="json")
    finally:
        session.close()
        ctx.engine.dispose()


@mcp_server.tool(description="Query a project's audit trail, optionally by event type or since a timestamp.")
@_map_errors
def get_audit_trail(
    project_id: str,
    event_type: str | None = None,
    since: str | None = None,
) -> list[dict[str, Any]]:
    """Return audit events (ISO-8601 ``since``, newest last)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            audit = AuditService(session, ctx.project.id)
            since_dt: datetime | None = None
            if since:
                try:
                    since_dt = datetime.fromisoformat(since)
                except ValueError as exc:
                    raise ValidationError(f"since must be ISO-8601, got {since!r}") from exc
            events, _total = audit.list_events(event_type=event_type, since=since_dt, limit=1000)
            return [_event_dict(e) for e in events]
        finally:
            session.close()


@mcp_server.tool(description="Compute inter-rater reliability (Cohen's kappa) between AI and human decisions.")
@_map_errors
def compute_irr(project_id: str) -> dict[str, Any]:
    """Return overall + per-code kappa and the number of compared segments."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            report = IRRCalculator(session).compute()
            return {
                "overall_kappa": report.overall_kappa,
                "per_code": report.per_code,
                "n_compared": report.n_compared,
            }
        finally:
            session.close()


@mcp_server.tool(
    description=(
        "Propose new codes from a project's uncoded/flagged segments. Proposals are "
        "suggestions only — never auto-added to the codebook."
    )
)
@_map_errors
def propose_codes(project_id: str, document_id: str | None = None) -> list[dict[str, Any]]:
    """Draft new code suggestions from uncoded segments (03_API_SPEC section 4)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            provider = _make_provider(ctx.config)
            try:
                loaded_pack = load_pack(
                    _resolve_pack_name(ctx, None), project_packs_dir=ctx.project_dir / "packs"
                )
                books = CodebookService(session, AuditService(session, ctx.project.id))
                books.ensure_codebook(ctx.project)
                audit = AuditService(session, ctx.project.id)
                svc = CodingService(session, audit, books, None, provider, ctx.config)
                return svc.propose_codes(ctx.project, loaded_pack, document_id=document_id)
            finally:
                provider.close()
        finally:
            session.close()


# =============================================================================
# Resources (03_API_SPEC section 4 URI templates)
# =============================================================================


@mcp_server.resource("qualagent://{project_id}/codebook", mime_type="application/json")
@_map_errors
def codebook_resource(project_id: str) -> str:
    """The current codebook tree as JSON (empty tree when none exists yet)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            svc = CodebookService(session, AuditService(session, ctx.project.id))
            books = svc.list_codebooks(ctx.project.id)
            if not books:
                tree: dict[str, Any] = {
                    "project_id": ctx.project.id,
                    "version": None,
                    "status": "empty",
                    "codes": [],
                }
            else:
                book = books[-1]
                tree = {
                    "id": book.id,
                    "project_id": book.project_id,
                    "version": book.version,
                    "status": book.status,
                    "codes": [_code_dict(c) for c in svc.codes(book)],
                }
            return json.dumps(tree, ensure_ascii=False)
        finally:
            session.close()


@mcp_server.resource("qualagent://{project_id}/audit", mime_type="application/x-ndjson")
@_map_errors
def audit_resource(project_id: str) -> str:
    """The project audit trail as JSONL (one event per line; a snapshot)."""
    with _using_project(project_id) as ctx:
        session = ctx.session()
        try:
            audit = AuditService(session, ctx.project.id)
            events, _total = audit.list_events(limit=10000)
            return "\n".join(
                json.dumps(_event_dict(e), ensure_ascii=False) for e in events
            )
        finally:
            session.close()


# =============================================================================
# Entry point
# =============================================================================


def main(transport: str = "stdio", *, host: str = "127.0.0.1", port: int = 8742) -> None:
    """Run the QualAgent MCP server (stdio by default; sse optional).

    Args:
        transport: ``"stdio"`` (default) or ``"sse"``.
        host: Bind host for the SSE transport.
        port: Bind port for the SSE transport.
    """
    if transport == "stdio":
        mcp_server.run(transport="stdio")
    elif transport == "sse":
        mcp_server.run(transport="sse", host=host, port=port)
    else:  # pragma: no cover - guarded by the CLI; defensive
        raise ValidationError(f"Unknown transport {transport!r}; choose stdio or sse")
