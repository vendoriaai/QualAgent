# qualagent.cli — Typer CLI

## Purpose

Command-line interface for QualAgent operations: project management, ingestion, coding, review, export, and IRR.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Entry point: `qualagent.cli.main.app` (Typer instance)
- Commands grouped: `project`, `ingest`, `code`, `review`, `export`, `irr`, `pack`
- Uses `qualagent.runtime.Runtime` for service access
- Output: human-readable tables (Rich) or JSON (`--json` flag)
- Configuration via `qualagent.config.Config` (env vars, .env file)

## Work Guidance

- Add commands by creating functions in `main.py` or submodules, decorating with `@app.command()`
- Use `Runtime` for all service access — do not instantiate services directly
- Integration tests in `tests/integration/test_cli_*.py`
- Follow existing patterns for argument parsing and error handling

## Verification

- `pytest tests/integration/test_cli_*.py`
- Manual: `python -m qualagent.cli --help`

## Child DOX Index

- No child AGENTS.md files needed