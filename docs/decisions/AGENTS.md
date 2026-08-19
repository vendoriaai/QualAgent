# docs/decisions — Architecture Decision Records

## Purpose

Immutable Architecture Decision Records (ADRs) documenting significant technical choices.

## Ownership

- Primary: QualAgent development team
- Root-level documentation (not part of `qualagent` package)

## Local Contracts

- Format: Markdown with standard ADR structure (Title, Status, Context, Decision, Consequences)
- Naming: `ADR-NNN.md` with zero-padded sequence number
- Status: `Proposed`, `Accepted`, `Deprecated`, `Superseded`
- Once `Accepted`, ADRs are immutable — create new ADR to supersede
- Current ADRs:
  - `ADR-001.md` — SQLite for local dev, PostgreSQL for production
  - `ADR-002.md` — SQLModel for ORM
  - `ADR-003.md` — Typer for CLI, FastAPI for REST
  - `ADR-004.md` — Pack-based methodology system

## Work Guidance

- Create new ADR for any decision affecting architecture, data model, external dependencies, or cross-cutting concerns
- Use template: `docs/decisions/ADR-template.md` (if exists) or copy latest ADR
- Link related ADRs in "Related" section
- Do not edit accepted ADRs — add new one with `Supersedes` reference

## Verification

- No automated verification — manual review during architecture discussions

## Child DOX Index

- No child AGENTS.md files needed