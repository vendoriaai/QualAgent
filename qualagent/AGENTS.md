# qualagent package

## Purpose

Core application package for QualAgent — a qualitative analysis platform with LLM-assisted coding, codebook management, inter-rater reliability, and export capabilities.

## Ownership

- Primary: QualAgent development team
- All submodules are owned by this package

## Local Contracts

- Public API surface: `qualagent.runtime` (Runtime class), `qualagent.config` (Config), `qualagent.domain` (models, schemas, errors)
- Internal modules are not guaranteed stable across versions
- Configuration via `qualagent.config.Config` (Pydantic Settings, env-file supported)

## Work Guidance

- Follow domain-driven structure: domain → services → api/cli → packs → storage → llm
- New features: add domain models first, then services, then API/CLI exposure
- LLM providers implement `qualagent.llm.base.LLMProvider` protocol
- Packs are loaded via `qualagent.packs.loader.load_pack`
- Database access through `qualagent.storage.db.Database` (SQLModel/SQLite)
- File storage through `qualagent.storage.files.FileStorage`

## Verification

- Unit tests in `tests/unit/` mirror package structure
- Integration tests in `tests/integration/`
- Golden tests for packs in `tests/golden/`
- Run: `pytest tests/` (requires `.venv`)

## Child DOX Index

- `api/` — FastAPI REST API (AGENTS.md)
- `cli/` — Typer CLI commands (AGENTS.md)
- `domain/` — Domain models, schemas, errors (AGENTS.md)
- `llm/` — LLM provider layer (AGENTS.md)
- `packs/` — Pack engine and builtin packs (AGENTS.md)
- `services/` — Business logic services (AGENTS.md)
- `storage/` — Database and file storage (AGENTS.md)