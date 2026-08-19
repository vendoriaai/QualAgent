# qualagent.domain — Domain Models, Schemas, Errors

## Purpose

Core domain types: SQLModel database models, Pydantic request/response schemas, and domain exceptions.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package — stable public API surface

## Local Contracts

- `models.py`: SQLModel tables (Project, Document, Segment, Code, Codebook, Coding, Review, IRRSession, etc.)
- `schemas.py`: Pydantic models for API/CLI boundaries (request/response, not DB)
- `errors.py`: Domain exceptions (`NotFoundError`, `ValidationError`, `ConflictError`, `LLMError`, `PackError`)
- Models use `qualagent.storage.db.Database` for persistence
- Schemas used by `qualagent.api` and `qualagent.cli` for validation

## Work Guidance

- Add new domain entities in `models.py` with SQLModel
- Add corresponding schemas in `schemas.py` for API/CLI
- Add domain-specific errors in `errors.py`
- Keep models and schemas separate — models for DB, schemas for boundaries
- Migrations: not yet implemented (SQLite dev DB recreated on schema change)

## Verification

- `pytest tests/unit/test_models_db.py`
- `pytest tests/unit/test_schemas.py`

## Child DOX Index

- No child AGENTS.md files needed