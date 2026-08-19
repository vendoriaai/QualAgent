# qualagent.api — FastAPI REST API

## Purpose

Exposes QualAgent functionality via HTTP/JSON for web clients and integrations.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Entry point: `qualagent.api.app.create_app()` returns FastAPI instance
- Routes grouped by domain: projects, documents, codes, coding, review, export, IRR
- Shared error mapping via `qualagent.domain.errors` → HTTP status codes
- Request/response validation via Pydantic models in `qualagent.domain.schemas`
- Authentication: not yet implemented (planned for future phase)

## Work Guidance

- Add new endpoints by creating route modules, registering in `app.py`
- Use dependency injection for services (see `qualagent.services`)
- Contract tests in `tests/integration/test_api_contract.py`
- Keep route handlers thin — delegate to services

## Verification

- `pytest tests/integration/test_api_contract.py`
- OpenAPI schema at `/openapi.json` when running

## Child DOX Index

- No child AGENTS.md files needed