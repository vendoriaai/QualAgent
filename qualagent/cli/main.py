"""QualAgent CLI entry point (Typer).

Global flags: ``--project-dir`` (study ``.qualagent`` directory), ``--json``
(machine-readable output), ``--verbose``.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from qualagent import __version__
from qualagent.config import config_to_dict, global_config_dir, load_config
from qualagent.domain.errors import QualAgentError, ValidationError
from qualagent.runtime import open_project
from qualagent.services.audit_service import AuditService
from qualagent.services.ingestion_service import IngestionService
from qualagent.services.segmentation_service import STRATEGIES, SegmentationService
from qualagent.storage.files import FileStore
from qualagent.storage.registry import ProjectRegistry

app = typer.Typer(
    name="qualagent",
    help="Methodology-faithful AI agent for qualitative data analysis.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="View or edit configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")
code_app = typer.Typer(help="Run and inspect coding runs.", no_args_is_help=True)
app.add_typer(code_app, name="code")
codebook_app = typer.Typer(help="Manage the codebook.", no_args_is_help=True)
app.add_typer(codebook_app, name="codebook")
packs_app = typer.Typer(help="List methodology packs.", no_args_is_help=True)
app.add_typer(packs_app, name="packs")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

#: Maps domain error codes to CLI exit codes (03_API_SPEC section 2).
_EXIT_CODES = {
    "CONFLICT": 2,
    "VALIDATION_ERROR": 3,
    "LLM_VALIDATION_FAILED": 4,
    "REMOTE_PROVIDER_NOT_ACCEPTED": 5,
    "CODEBOOK_LOCKED": 6,
}

ProjectDir = Annotated[
    Path,
    typer.Option(
        "--project-dir",
        help="Path to the study's .qualagent directory.",
        show_default="./.qualagent",
    ),
]
AsJson = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]
Verbose = Annotated[bool, typer.Option("--verbose", help="Verbose logging.")]


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"qualagent {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    project_dir: ProjectDir = Path(".qualagent"),
    as_json: AsJson = False,
    verbose: Verbose = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_version_callback, is_eager=True),
    ] = False,
) -> None:
    """QualAgent: defensible, audited, AI-assisted qualitative coding."""
    ctx.obj = {
        "project_dir": project_dir,
        "json": as_json,
        "verbose": verbose,
    }


def _fail(exc: QualAgentError) -> None:
    """Print a structured CLI error and exit with the code from 03_API_SPEC."""
    err_console.print(f"[red]error[/red] {exc.error_code}: {exc}")
    raise typer.Exit(code=_EXIT_CODES.get(exc.error_code, 1))


def _registry() -> ProjectRegistry:
    """The machine-level project registry."""
    return ProjectRegistry(global_config_dir() / "registry.json")


def _ingestion(ctx: typer.Context) -> tuple[Any, IngestionService]:
    """Open the current study and build an IngestionService bound to it."""
    project_dir: Path = ctx.obj["project_dir"]
    pctx = open_project(project_dir)
    session = pctx.session()
    audit = AuditService(session, pctx.project.id)
    segmentation = SegmentationService(session)
    file_store = FileStore(project_dir)
    ingestion = IngestionService(session, file_store, audit, segmentation)
    return pctx, ingestion


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print the resolved configuration (defaults + global + project)."""
    project_dir: Path = ctx.obj["project_dir"]
    as_json: bool = ctx.obj["json"]
    try:
        cfg = load_config(project_dir if project_dir.exists() else None)
    except ValidationError as exc:
        _fail(exc)
        raise  # unreachable; keeps mypy happy
    data = config_to_dict(cfg)
    if as_json:
        console.print_json(json.dumps(data))
        return
    table = Table(title="QualAgent configuration")
    table.add_column("Section")
    table.add_column("Key")
    table.add_column("Value")
    for section, values in data.items():
        for key, value in values.items():
            table.add_row(section, key, str(value))
    console.print(table)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Dotted key, e.g. llm.model")],
    value: Annotated[str, typer.Argument(help="Value (bool/int/float/str parsed automatically)")],
) -> None:
    """Set a project-level config value in {project_dir}/config.toml."""
    import tomli_w

    project_dir: Path = ctx.obj["project_dir"]
    if not project_dir.is_dir():
        _fail(
            ValidationError(
                f"Project directory {project_dir} not found. Run 'qualagent init' first."
            )
        )
    config_path = project_dir / "config.toml"
    original_bytes = config_path.read_bytes() if config_path.is_file() else None
    current: dict[str, Any] = {}
    if original_bytes is not None:
        with config_path.open("rb") as fh:
            current = tomllib.load(fh)

    parts = key.split(".")
    if not 2 <= len(parts) <= 3 or not all(parts):
        _fail(ValidationError("Key must look like 'section.key' or 'section.subsection.key'"))
    node: dict[str, Any] = current
    for part in parts[:-1]:
        node = node.setdefault(part, {})
        if not isinstance(node, dict):
            _fail(ValidationError(f"Cannot set {key}: {part} is not a section"))
    node[parts[-1]] = _parse_value(value)

    config_path.write_bytes(tomli_w.dumps(current).encode("utf-8"))
    # Re-validate the merged result; roll back if the new value is invalid.
    try:
        load_config(project_dir)
    except ValidationError as exc:
        if original_bytes is not None:
            config_path.write_bytes(original_bytes)
        else:
            config_path.unlink(missing_ok=True)
        _fail(exc)
    console.print(f"[green]Set[/green] {key} = {node[parts[-1]]!r} in {config_path}")


@code_app.command("run")
def code_run(
    ctx: typer.Context,
    pack: Annotated[str | None, typer.Option("--pack", help="Pack name@version")] = None,
    codebook_version: Annotated[
        int | None, typer.Option("--codebook", help="Codebook version to code against")
    ] = None,
    batch_size: Annotated[int | None, typer.Option("--batch-size", min=1)] = None,
    accept_remote: Annotated[
        bool, typer.Option("--accept-remote", help="Consent to send data to a hosted provider")
    ] = False,
    llm: Annotated[
        str, typer.Option("--llm", help="Provider: config default | ollama | openai | fake")
    ] = "config",
    cassette: Annotated[
        Path | None,
        typer.Option("--cassette", help="YAML cassette for --llm fake (also: record:PATH)"),
    ] = None,
    document_id: Annotated[str | None, typer.Option("--document")] = None,
) -> None:
    """Execute a coding run over pending segments."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    as_json: bool = ctx.obj["json"]
    from qualagent.llm.base import LLMProvider
    from qualagent.llm.factory import create_provider
    from qualagent.llm.fake import FakeProvider
    from qualagent.services.codebook_service import CodebookService
    from qualagent.services.coding_service import CodingService

    provider: LLMProvider
    record_path: Path | None = None
    try:
        if llm == "fake":
            kwargs: dict[str, Any] = {}
            if cassette is not None:
                if str(cassette).startswith("record:"):
                    record_path = Path(str(cassette)[7:])
                else:
                    kwargs["cassette_path"] = cassette
            provider = FakeProvider(**kwargs)
        else:
            config = pctx.config if llm == "config" else load_config(project_dir=None)
            provider = create_provider(config, cli_accepted_remote=accept_remote)
        session = pctx.session()
        audit = AuditService(session, pctx.project.id)
        books = CodebookService(session, audit)
        books.ensure_codebook(pctx.project)
        pack_name = pack or (pctx.project.active_pack or "open_coding").split("@")[0]
        from qualagent.packs.loader import load_pack as _load_pack

        loaded_pack = _load_pack(pack_name, project_packs_dir=pctx.project_dir / "packs")
        run = CodingService(session, audit, books, None, provider, pctx.config).start_run(
            pctx.project,
            loaded_pack,
            codebook_version=codebook_version,
            batch_size=batch_size,
            document_id=document_id,
        )
        stats = json.loads(run.stats_json or "{}")
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    finally:
        if llm != "fake" and not isinstance(provider, FakeProvider):
            provider.close()
    payload = {"run_id": run.id, "status": run.status, "stats": stats}
    if record_path is not None:
        payload["recorded_cassette"] = str(record_path)
    if as_json:
        console.print_json(json.dumps(payload))
    else:
        console.print(f"[green]run[/green] {run.id} -> {run.status}")
        table = Table(title="Run stats")
        table.add_column("Metric")
        table.add_column("Value")
        for key, value in stats.items():
            table.add_row(key, str(value))
        console.print(table)


@code_app.command("status")
def code_status(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Coding run id")],
) -> None:
    """Show a coding run's progress/stats."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.llm.fake import FakeProvider
    from qualagent.services.coding_service import CodingService

    with pctx.session() as session:
        try:
            run = CodingService(
                session,
                AuditService(session, pctx.project.id),
                None,
                None,
                FakeProvider([]),
                pctx.config,
            ).get_run(run_id)
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
        stats = json.loads(run.stats_json or "{}")
    if ctx.obj["json"]:
        console.print_json(
            json.dumps(
                {
                    "run_id": run.id,
                    "status": run.status,
                    "pack": run.pack,
                    "model": run.model,
                    "stats": stats,
                }
            )
        )
    else:
        console.print(f"run {run.id}: [bold]{run.status}[/bold] pack={run.pack} model={run.model}")
        console.print_json(run.stats_json or "{}")


@app.command()
def init(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Study name; creates ./NAME/.qualagent")],
    pack: Annotated[
        str | None,
        typer.Option("--pack", help="Active methodology pack, e.g. thematic_analysis@1.0.0"),
    ] = None,
    study_root: Annotated[
        Path | None,
        typer.Option("--dir", help="Parent directory for the study (default: current dir)"),
    ] = None,
) -> None:
    """Create a new study with database, files, and audit trail."""
    from qualagent.services.project_service import ProjectService

    root = study_root if study_root is not None else Path.cwd()
    service = ProjectService(_registry())
    try:
        pctx = service.create(name, study_root=root, pack=pack)
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    as_json: bool = ctx.obj["json"]
    if as_json:
        console.print_json(
            json.dumps(
                {
                    "project_id": pctx.project.id,
                    "name": pctx.project.name,
                    "path": str(pctx.project_dir),
                }
            )
        )
    else:
        console.print(
            f"[green]Created study[/green] [bold]{name}[/bold] "
            f"(id {pctx.project.id}) at {pctx.project_dir}"
        )
        if pack:
            console.print(f"  active pack: {pack}")


@app.command("import")
def import_(
    ctx: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Files to import")],
    strategy: Annotated[
        str,
        typer.Option("--strategy", help=f"Segmentation strategy: {', '.join(STRATEGIES)}"),
    ] = "sentence",
) -> None:
    """Ingest files into the current study and segment them."""
    if strategy not in STRATEGIES:
        _fail(
            ValidationError(f"Unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
        )
    try:
        pctx, ingestion = _ingestion(ctx)
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    as_json: bool = ctx.obj["json"]
    results: list[dict[str, Any]] = []
    with pctx.session() as _keepalive:  # keep engine alive during the loop
        for path in paths:
            try:
                document, segments = ingestion.ingest_file(
                    pctx.project.id,
                    path,
                    strategy=strategy,  # type: ignore[arg-type]
                )
            except QualAgentError as exc:
                _fail(exc)
                raise  # unreachable
            results.append(
                {
                    "document_id": document.id,
                    "filename": document.filename,
                    "segment_count": len(segments),
                }
            )
    if as_json:
        console.print_json(json.dumps(results))
    else:
        for row in results:
            console.print(
                f"[green]imported[/green] {row['filename']} "
                f"-> {row['document_id']} ({row['segment_count']} segments)"
            )


@app.command()
def segment(
    ctx: typer.Context,
    document_id: Annotated[str, typer.Argument(help="Document to re-segment")],
    strategy: Annotated[
        str,
        typer.Option("--strategy", help=f"Strategy: {', '.join(STRATEGIES)}"),
    ] = "sentence",
) -> None:
    """Re-run segmentation on a document with a new strategy."""
    if strategy not in STRATEGIES:
        _fail(
            ValidationError(f"Unknown strategy {strategy!r}; choose from {', '.join(STRATEGIES)}")
        )
    try:
        pctx, ingestion = _ingestion(ctx)
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    with pctx.session() as _keepalive:
        try:
            segments = ingestion.resegment(document_id, strategy)  # type: ignore[arg-type]
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
    as_json: bool = ctx.obj["json"]
    if as_json:
        console.print_json(
            json.dumps(
                {"document_id": document_id, "strategy": strategy, "segment_count": len(segments)}
            )
        )
    else:
        console.print(
            f"[green]re-segmented[/green] {document_id} with {strategy} -> {len(segments)} segments"
        )


def _parse_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


@codebook_app.command("show")
def codebook_show(
    ctx: typer.Context,
    version: Annotated[int | None, typer.Option("--version", help="Codebook version")] = None,
) -> None:
    """Print the codebook (with codes) as Markdown or JSON."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.codebook_service import CodebookService

    with pctx.session() as session:
        books = CodebookService(session, AuditService(session, pctx.project.id))
        try:
            book = books.get_by_version(pctx.project.id, version)
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
        codes = books.codes(book)
        if ctx.obj["json"]:
            console.print_json(
                json.dumps(
                    {
                        "id": book.id,
                        "version": book.version,
                        "status": book.status,
                        "codes": [
                            {
                                "name": c.name,
                                "definition": c.definition,
                                "inclusion": c.inclusion_criteria,
                                "exclusion": c.exclusion_criteria,
                            }
                            for c in codes
                        ],
                    }
                )
            )
        else:
            console.print(f"# Codebook v{book.version} ({book.status})")
            for c in codes:
                marker = " [dim](suggestion)[/dim]" if c.name.startswith("suggestion_") else ""
                console.print(f"- [bold]{c.name}[/bold]{marker}: {c.definition}")


@codebook_app.command("refine")
def codebook_refine(
    ctx: typer.Context,
    from_suggestions: Annotated[
        bool, typer.Option("--from-suggestions", help="Promote AI suggestions to codes")
    ] = False,
) -> None:
    """Create a refined codebook version (Braun & Clarke phase 3)."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.codebook_service import CodebookService

    with pctx.session() as session:
        books = CodebookService(session, AuditService(session, pctx.project.id))
        try:
            book = books.refine(pctx.project, from_suggestions=from_suggestions)
            version, status = book.version, book.status
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
    if ctx.obj["json"]:
        console.print_json(json.dumps({"version": version, "status": status}))
    else:
        console.print(f"[green]refined[/green] -> codebook v{version}")


@codebook_app.command("lock")
def codebook_lock(
    ctx: typer.Context,
    version: Annotated[int | None, typer.Option("--version", help="Version to lock")] = None,
) -> None:
    """Lock a codebook version (snapshots it)."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.codebook_service import CodebookService

    with pctx.session() as session:
        books = CodebookService(session, AuditService(session, pctx.project.id))
        try:
            book = books.lock(pctx.project, version)
            version_, status = book.version, book.status
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
    if ctx.obj["json"]:
        console.print_json(json.dumps({"version": version_, "status": status}))
    else:
        console.print(f"[green]locked[/green] codebook v{version_}")


@packs_app.command("list")
def packs_list(ctx: typer.Context) -> None:
    """List builtin + project packs."""
    from qualagent.packs.loader import discover_packs

    project_dir: Path = ctx.obj["project_dir"]
    dirs = discover_packs(project_dir / "packs" if project_dir.exists() else None)
    if ctx.obj["json"]:
        console.print_json(json.dumps(sorted(dirs)))
    else:
        table = Table(title="Methodology packs")
        table.add_column("name")
        table.add_column("origin")
        for name in sorted(dirs):
            origin = "project" if (project_dir / "packs" / name).exists() else "builtin"
            table.add_row(name, origin)
        console.print(table)


@packs_app.command("show")
def packs_show(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Pack name (optionally name@version)")],
) -> None:
    """Show pack details (citation, taxonomy, questions)."""
    from qualagent.packs.loader import load_pack

    project_dir: Path = ctx.obj["project_dir"]
    try:
        pack = load_pack(
            name, project_packs_dir=project_dir / "packs" if project_dir.exists() else None
        )
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    if ctx.obj["json"]:
        console.print_json(
            json.dumps(
                {
                    "id": pack.id,
                    "title": pack.title,
                    "citation": pack.citation,
                    "multi_code": pack.multi_code,
                    "categories": pack.category_paths(),
                }
            )
        )
    else:
        console.print(f"[bold]{pack.title}[/bold] ({pack.id})")
        console.print(f"citation: {pack.citation}")
        console.print("categories:")
        for path in pack.category_paths():
            console.print(f"  - {path}")


review_app = typer.Typer(help="Review AI assignments.", no_args_is_help=True)
app.add_typer(review_app, name="review")
irr_app = typer.Typer(help="Inter-rater reliability.", no_args_is_help=True)
app.add_typer(irr_app, name="irr")
export_app = typer.Typer(help="Export project artifacts.", no_args_is_help=True)
app.add_typer(export_app, name="export")


@review_app.command("list")
def review_list(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option(help="pending|approved|rejected|edited")] = None,
    code: Annotated[str | None, typer.Option(help="Filter by code name")] = None,
    min_confidence: Annotated[float, typer.Option(help="Minimum confidence")] = 0.0,
    max_confidence: Annotated[float, typer.Option(help="Maximum confidence")] = 1.0,
    limit: Annotated[int, typer.Option(help="Page size")] = 20,
    offset: Annotated[int, typer.Option(help="Page offset")] = 0,
) -> None:
    """List assignments for human review."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.review_service import ReviewService

    with pctx.session() as session:
        svc = ReviewService(session, AuditService(session, pctx.project.id))
        try:
            items, total = svc.list_items(
                status=status,
                code=code,
                min_confidence=min_confidence,
                max_confidence=max_confidence,
                limit=limit,
                offset=offset,
            )
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
        if ctx.obj["json"]:
            console.print_json(
                json.dumps(
                    {
                        "total": total,
                        "items": [
                            {
                                "assignment_id": it.assignment.id,
                                "segment_id": it.assignment.segment_id,
                                "code": it.code_name,
                                "status": it.assignment.status,
                                "confidence": it.assignment.confidence,
                                "rationale": it.assignment.rationale,
                                "segment_text": it.segment_text,
                            }
                            for it in items
                        ],
                    }
                )
            )
        else:
            table = Table(title=f"Assignments for review ({total} total)")
            for col_name in ("id", "code", "status", "conf", "segment"):
                table.add_column(col_name)
            for it in items:
                conf = f"{it.assignment.confidence:.2f}" if it.assignment.confidence else ""
                text = it.segment_text.replace("\n", " ")[:40]
                table.add_row(
                    it.assignment.id[:8], it.code_name or "?", it.assignment.status, conf, text
                )
            console.print(table)


def _review_service(ctx: typer.Context, with_memory: bool) -> tuple[Any, Any, Any]:
    """Open a ReviewService bound to the current study."""
    pctx = open_project(ctx.obj["project_dir"])
    session = pctx.session()
    audit = AuditService(session, pctx.project.id)
    memory = None
    if with_memory:
        from qualagent.services.memory_service import MemoryService

        memory = MemoryService(session, pctx.project.id, pctx.project_dir, pctx.config)
    from qualagent.services.review_service import ReviewService

    return pctx, session, ReviewService(session, audit, memory)


@review_app.command("decide")
def review_decide(
    ctx: typer.Context,
    assignment_id: Annotated[str, typer.Argument(help="Assignment to decide")],
    approve: Annotated[bool, typer.Option("--approve", help="Approve the assignment")] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the assignment")] = False,
    edit_code: Annotated[str | None, typer.Option(help="Reassign to this code")] = None,
    note: Annotated[str, typer.Option(help="Reviewer note")] = "",
) -> None:
    """Record a human decision on one assignment (approve/reject/edit)."""
    if sum(bool(x) for x in (approve, reject, edit_code)) != 1:
        err_console.print("[red]error[/red] pick exactly one of --approve/--reject/--edit-code")
        raise typer.Exit(code=3)
    try:
        pctx, session, svc = _review_service(ctx, with_memory=edit_code is not None)
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    try:
        if approve:
            row = svc.approve(assignment_id, note=note)
        elif reject:
            row = svc.reject(assignment_id, note=note)
        elif edit_code is not None:
            row = svc.edit_code(assignment_id, new_code_name=edit_code, note=note)
        else:  # pragma: no cover - guarded above
            raise ValidationError("no decision flag")
        status, aid = row.status, row.id
    except QualAgentError as exc:
        session.close()
        pctx.engine.dispose()
        _fail(exc)
        raise  # unreachable
    session.close()
    pctx.engine.dispose()
    if ctx.obj["json"]:
        console.print_json(json.dumps({"assignment_id": aid, "status": status}))
    else:
        console.print(f"[green]{status}[/green] assignment {aid[:8]}")


@irr_app.command("compute")
def irr_compute(
    ctx: typer.Context,
    subset: Annotated[str, typer.Option(help="verified")] = "verified",
) -> None:
    """Cohen's kappa, AI vs human, per code and overall."""
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.irr_service import IRRCalculator

    with pctx.session() as session:
        report = IRRCalculator(session).compute()
        payload = {
            "overall_kappa": report.overall_kappa,
            "per_code": report.per_code,
            "n_compared": report.n_compared,
        }
    if ctx.obj["json"]:
        console.print_json(json.dumps(payload))
    else:
        console.print(f"overall kappa: [bold]{report.overall_kappa:.2f}[/bold]")
        console.print(f"compared segments: {report.n_compared} ({subset})")
        for name, value in report.per_code.items():
            console.print(f"  {name}: {value:.2f}")


@export_app.command("matrix")
def export_matrix(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Output file")],
    fmt: Annotated[str, typer.Option("--format", help="csv|xlsx")] = "csv",
) -> None:
    """Export the code x document frequency matrix."""
    _run_export(ctx, "matrix", out, fmt)


@export_app.command("codebook")
def export_codebook(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Output file")],
    fmt: Annotated[str, typer.Option("--format", help="md|pdf")] = "md",
) -> None:
    """Export the codebook document."""
    _run_export(ctx, "codebook", out, fmt)


@export_app.command("audit")
def export_audit(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Output file")],
    fmt: Annotated[str, typer.Option("--format", help="jsonl|pdf")] = "jsonl",
) -> None:
    """Export the full audit trail."""
    _run_export(ctx, "audit", out, fmt)


@export_app.command("methods")
def export_methods(
    ctx: typer.Context,
    out: Annotated[Path, typer.Option("--out", help="Output file")],
) -> None:
    """Export the draft methods-section paragraph (MD)."""
    _run_export(ctx, "methods", out, "md")


def _run_export(ctx: typer.Context, kind: str, out: Path, fmt: str) -> None:
    try:
        pctx = open_project(ctx.obj["project_dir"])
    except QualAgentError as exc:
        _fail(exc)
        raise  # unreachable
    from qualagent.services.export_service import ExportService

    with pctx.session() as session:
        svc = ExportService(session, AuditService(session, pctx.project.id))
        try:
            if kind == "matrix":
                exported = svc.export_matrix(out, fmt=fmt)
            elif kind == "codebook":
                exported = svc.export_codebook(out, fmt=fmt)
            elif kind == "audit":
                exported = svc.export_audit(out, fmt=fmt)
            else:
                exported = svc.export_methods(out)
        except QualAgentError as exc:
            _fail(exc)
            raise  # unreachable
    if ctx.obj["json"]:
        console.print_json(
            json.dumps(
                {"kind": exported.kind, "format": exported.format, "path": str(exported.path)}
            )
        )
    else:
        console.print(f"[green]exported[/green] {exported.kind} -> {exported.path}")


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Bind port")] = 8741,
) -> None:
    """Start the REST API server (03_API_SPEC section 3)."""
    import uvicorn

    from qualagent.api.app import app as api_app

    console.print(f"[green]serving[/green] QualAgent API on http://{host}:{port}/docs")
    uvicorn.run(api_app, host=host, port=port, log_level="info")


@app.command()
def mcp(
    transport: Annotated[
        str, typer.Option("--transport", help="stdio (default) | sse")
    ] = "stdio",
    host: Annotated[str, typer.Option(help="SSE bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="SSE bind port")] = 8742,
) -> None:
    """Start the MCP server (03_API_SPEC section 4)."""
    from qualagent.mcp_server.server import main as mcp_main

    if transport != "stdio":
        console.print(f"[green]serving[/green] QualAgent MCP (SSE) on http://{host}:{port}")
    mcp_main(transport=transport, host=host, port=port)


if __name__ == "__main__":
    app()
