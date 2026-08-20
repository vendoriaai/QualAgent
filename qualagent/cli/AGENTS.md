# qualagent.cli — Typer CLI

## Purpose

Command-line interface for QualAgent operations: project management, ingestion, coding, review, export, and IRR.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Entry point: `qualagent.cli.main.app` (Typer instance)
- Commands grouped: `init`, `import`, `segment`, `code`, `codebook`, `review`,
  `irr`, `export`, `packs`, `config`, `serve`, `mcp`
- `serve` starts the REST API (03_API_SPEC section 3); `mcp` starts the MCP
  server (03_API_SPEC section 4, `qualagent mcp --transport stdio|sse`)
- Uses `qualagent.runtime.open_project` for service access
- Output: human-readable tables (Rich) or JSON (`--json` flag)
- Domain errors map to CLI exit codes per 03_API_SPEC section 2

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