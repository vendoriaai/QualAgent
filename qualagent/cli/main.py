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
from qualagent.config import config_to_dict, load_config
from qualagent.domain.errors import QualAgentError, ValidationError

app = typer.Typer(
    name="qualagent",
    help="Methodology-faithful AI agent for qualitative data analysis.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="View or edit configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

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
    raise typer.Exit(code=1)


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


if __name__ == "__main__":
    app()
