# tests — Test Suite

## Purpose

Automated verification for QualAgent: unit, integration, and golden tests.

## Ownership

- Primary: QualAgent development team
- Root-level test directory (not part of `qualagent` package)

## Local Contracts

- `conftest.py`: pytest fixtures (temp DB, Runtime, FakeProvider, sample data)
- `fixtures.py`: shared test data factories
- `unit/`: isolated unit tests mirroring `qualagent/` structure
  - One test file per module under test
- `integration/`: end-to-end tests via API and CLI
  - `test_api_contract.py` — OpenAPI contract validation
  - `test_cli_*.py` — CLI command flows
- `golden/`: deterministic output tests for packs
  - `test_packs_golden.py` — compares pack outputs to recorded goldens
- Run: `pytest tests/` (requires `.venv` with dev dependencies)

## Work Guidance

- Add unit tests alongside new code in `unit/`
- Add integration tests for new API/CLI features
- Record golden outputs via `pytest tests/golden/ --update-goldens`
- Use `FakeProvider` with cassettes for deterministic LLM tests
- Fixtures in `conftest.py` provide isolated DB per test

## Verification

- `pytest tests/` — full suite
- `pytest tests/unit/` — unit only
- `pytest tests/integration/` — integration only
- `pytest tests/golden/` — golden only

## Child DOX Index

- No child AGENTS.md files needed