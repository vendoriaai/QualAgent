# AI Builder Instructions (System-Level Brief)
# Read this FIRST. You are the engineering agent building QualAgent.

You are implementing **QualAgent**, an open-source, local-first AI agent for qualitative data analysis. This document governs HOW you build; documents 01–06 define WHAT you build.

## 1. Document Map (read in this order)

| Doc | Contains | Authority |
|---|---|---|
| `00_BUILDER_INSTRUCTIONS.md` | Process, conventions, rules (this file) | Governs all work |
| `01_PRD.md` | Product scope, requirements (FR/NFR), non-goals | Scope disputes → PRD wins |
| `02_TAD.md` | Architecture, stack, layout, design decisions D1–D8 | Technical disputes → TAD wins |
| `03_API_SPEC.md` | CLI / REST / MCP contracts, error codes | Interface disputes → API spec wins |
| `04_DATA_MODEL.md` | Entities, schemas, pack format, config, exports | Data disputes → this wins |
| `05_IMPLEMENTATION_ROADMAP.md` | Phased tasks + acceptance criteria | Execution order — follow strictly |
| `06_README_DRAFT.md` | Final README content | Phase 9 only |

If documents conflict, apply the authority order above, fix the lower-authority doc in the same commit, and note the change in the commit message.

## 2. Hard Rules (never violate)

1. **Follow the roadmap phases in order.** A phase is done only when every acceptance criterion passes. Never skip ahead.
2. **Tests are part of the task, not a follow-up.** No task is complete without its tests passing in CI.
3. **Respect the non-goals (01_PRD §4).** Do not build a GUI, transcription, multi-user features, or a SaaS layer, even if it seems easy.
4. **No agent frameworks.** No LangChain/LangGraph/CrewAI/AutoGen. The coding pipeline is a deterministic orchestration loop (TAD D1). Do not add autonomous looping, self-directed tool calling, or replanning logic.
5. **Packs are data.** Never hardcode framework logic in Python. All framework knowledge lives in pack YAML files (TAD D2).
6. **Audit everything.** Every state-changing operation emits an AuditEvent via `AuditService.emit()`. Never write update/delete paths for the audit table.
7. **Immutable segments.** Never mutate offsets or raw text after ingestion (TAD D3).
8. **Privacy gate.** Remote LLM calls are impossible without explicit user consent (`accepted = true` or `--accept-remote`) (TAD D8).
9. **Secrets in env vars only.** Never write API keys to config files, logs, audit events, or tests.
10. **Structured output discipline.** All LLM calls go through `llm/structured.py` with a JSON Schema. No free-text parsing, no regex-over-LLM-output.

## 3. Coding Conventions

- Python 3.12+, full type hints; `mypy --strict` on `domain/`, `services/`, `llm/`.
- `ruff` for lint + format, line length 100.
- Google-style docstrings on all public functions/classes.
- Datetime: always timezone-aware UTC.
- Errors: raise domain exceptions from `domain/errors.py`; interface layers map them to the shared error codes in 03_API_SPEC §1.
- Logging: `structlog`, JSON lines, never log document content or prompts at INFO when provider is remote (hashes only).
- Dependency rule: interfaces → services → domain/infrastructure. Domain never imports FastAPI/Typer/MCP.

## 4. Git Discipline

- Branch per phase: `phase-1-core`, `phase-2-ingestion`, etc. Squash-merge to `main` when acceptance criteria pass.
- Commit format: `type(scope): message` — e.g. `feat(coding): add batch token budget`, `test(golden): van leeuwen fixture`, `fix(audit): enforce append-only trigger`.
- One task = one or more commits, but never mix tasks in one commit.
- Update `05_IMPLEMENTATION_ROADMAP.md` checkboxes as you complete tasks.

## 5. Testing Standards

- Fake LLM first: all tests run offline with the scripted fake provider (TAD §3.4). Real-provider tests are marked `@pytest.mark.live` and excluded from CI.
- Golden tests use recorded cassettes; re-recording requires bumping the pack version.
- Coverage gate: ≥ 80% line coverage on `domain/`, `services/`, `llm/` (enforced in CI).
- The export fidelity test (roadmap 5.5) and the segment offset invariant (2.3) are release-blocking; never mark them xfail.

## 6. When You Get Stuck

1. Re-read the relevant spec section; the answer is usually there.
2. If the spec is genuinely ambiguous, choose the option that best serves the PRD goals (defensibility, reproducibility, privacy), implement it, and document the decision in `docs/decisions/ADR-NNN.md` (one-page Architecture Decision Record: context, options, decision, consequences).
3. Never silently deviate from a spec.

## 7. First Action

Begin with Phase 0, Task 0.1. Report after each phase: what was built, test results, coverage %, and any ADRs written.
