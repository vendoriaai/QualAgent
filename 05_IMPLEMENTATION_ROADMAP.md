# Implementation Roadmap & Task Breakdown
# QualAgent — Ordered Build Plan for the AI Builder

**Version:** 1.0 · **Read first:** 00_BUILDER_INSTRUCTIONS.md, then 01–04.

Build strictly in phase order. Do not start a phase until every acceptance criterion of the previous phase passes. After each task, run its verification command and commit with the message format in 00_BUILDER_INSTRUCTIONS.md.

---

## Phase 0 — Project Scaffolding (Tasks 0.1–0.4)

- [x] 0.1 Create repository layout exactly as in 02_TAD §3. `pyproject.toml` with hatchling, deps: typer, rich, fastapi, uvicorn, mcp, sqlmodel, chromadb, sentence-transformers, pydantic>=2, python-docx, pypdf, srt, pandas, openpyxl, reportlab, scikit-learn, httpx. Dev deps: pytest, pytest-asyncio, respx, ruff, mypy, pre-commit. *(sentence-transformers moved to optional extra per ADR-001)*
- [x] 0.2 Configure ruff (line-length 100), mypy strict for `qualagent/domain`, `qualagent/services`, `qualagent/llm`; pre-commit hooks; GitHub Actions CI skeleton (lint → typecheck → test).
- [x] 0.3 Implement `config.py` (global + project TOML loading, env-var secrets, remote-provider gate).
- **Acceptance:** `pip install -e .` succeeds; `qualagent --help` runs; CI green on empty test suite.

## Phase 1 — Domain Core & Storage (Tasks 1.1–1.5)

- [x] 1.1 Implement all SQLModel entities per 04_DATA_MODEL §1, including the append-only SQLite triggers on `auditevent`.
- [x] 1.2 `storage/db.py`: engine per project (`{project}/.qualagent/qualagent.db`, WAL mode), session factory, auto-create schema + triggers.
- [x] 1.3 `storage/files.py`: store original files under `{project}/.qualagent/files/{sha256}` with computed hashes.
- [x] 1.4 `domain/schemas.py`: all Pydantic schemas; LLM-facing schemas with `extra="forbid"`.
- [x] 1.5 `services/project_service.py` + `services/audit_service.py` (create/read only; helper `emit(event_type, payload)` used by all all services). *(plus storage/registry.py + runtime.py per ADR-004)*
- **Acceptance:** unit tests pass for entity CRUD, trigger enforcement (UPDATE and DELETE on auditevent both raise), project create/list/delete emits audit events. Coverage ≥ 80% on touched modules.

## Phase 2 — Ingestion & Segmentation (Tasks 2.1–2.4)

- [x] 2.1 `services/ingestion_service.py`: parse TXT, DOCX (python-docx), PDF (pypdf), SRT (srt, strip timestamps, keep speaker if present), CSV (column `text`, optional `speaker`). Normalize Unicode (NFC), compute SHA-256 of original bytes. *(fixtures generated at test time; no binary blobs in repo)*
- [x] 2.2 `services/segmentation_service.py`: four strategies — `sentence` (regex-based, abbreviation-safe), `paragraph` (double-newline), `utterance` (single newline), `turns` (interview format `Speaker:` prefixes). All compute `start_offset`/`end_offset` into `raw_text`; record `segmenter_version`.
- [x] 2.3 Invariant test: for every segment, `document.raw_text[s.start_offset:s.end_offset]` round-trips and is non-empty.
- [x] 2.4 CLI: `init`, `import`, `segment` per 03_API_SPEC §2 with Rich progress output.
- **Acceptance:** `qualagent init demo && qualagent import fixtures/interview.docx` prints document id + segment count; offsets invariant test passes on all fixture files (create 5 fixtures: txt, docx, pdf, srt, csv).

## Phase 3 — LLM Layer (Tasks 3.1–3.4)

- [x] 3.1 `llm/base.py`: `LLMProvider` protocol — `complete(messages, *, schema: dict, temperature, seed) -> LLMResult(text, model, tokens, raw)`.
- [x] 3.2 Providers: `openai_provider.py` (JSON-schema response format), `anthropic_provider.py` (tool-use forced JSON), `gemini_provider.py` (response_schema), `ollama_provider.py` (format=json via /api/chat). Each records exact model string.
- [x] 3.3 `llm/structured.py`: `structured_call()` — validate against schema; on failure, one corrective retry appending the validation error; second failure raises `LLMValidationFailed`. Emit `llm.call` audit event per attempt (hash prompts when provider is remote).
- [x] 3.4 Fake provider for tests: scripted responses from YAML cassettes; record/replay support for golden tests.
- **Acceptance:** unit tests for retry logic (valid-first-try, repair-once, double-failure); no-network test proves Ollama path works with mocked socket; remote providers raise `REMOTE_PROVIDER_NOT_ACCEPTED` without the gate flag.

## Phase 4 — Pack Engine & Coding Pipeline (Tasks 4.1–4.6)

- [x] 4.1 `packs/loader.py`: discovery (builtin + project), validation per 04_DATA_MODEL §3 rules, version pinning.
- [x] 4.2 `packs/engine.py`: render pack → system prompt (instructions + category tree + decision questions + few-shots) and expose `output_schema`.
- [x] 4.3 `services/memory_service.py`: ChromaDB per project; `add_correction()`, `retrieve(query, k)`; embedding via ChromaDB's built-in ONNX MiniLM (offline, ADR-001), sentence-transformers optional.
- [x] 4.4 `services/codebook_service.py`: draft/refine/lock lifecycle; new-code proposals become draft suggestions only; lock snapshots.
- [x] 4.5 `services/coding_service.py`: pipeline per 02_TAD §6 — batching by count and token budget, prompt assembly (pack prompt + code definitions + top-k memories + segments with ids), structured call, persist assignments, flag failures, run stats.
- [x] 4.6 CLI: `code run`, `code status`, `packs list/show`, `codebook show/refine/lock`.
- **Acceptance:** end-to-end integration test — fixture interview, `open_coding` pack, fake LLM cassette → assignments persisted with rationale/confidence, audit trail complete, flagged segments on double validation failure; `qualagent code run` demo works offline with cassette mode (`--llm fake`).

## Phase 5 — Review, IRR & Exports (Tasks 5.1–5.5)

- [x] 5.1 `services/review_service.py`: list with filters; `decide()` implements approve/reject/edit; edits write a `MemoryItem` (kind=correction) capturing old→new code + note.
- [x] 5.2 `services/irr_service.py`: Cohen's kappa per code and overall between AI assignments and human-approved/edited assignments (sklearn).
- [x] 5.3 `services/export_service.py`: all four exports per 04_DATA_MODEL §5. Methods-paragraph template includes pack citation, model, temperature, dates, counts, kappa, oversight statement.
- [x] 5.4 CLI: `review list/decide`, `irr compute`, `export *`.
- [x] 5.5 Export fidelity test: every example quote in exported codebook must appear at the recorded offsets in its source document (automated assertion — this is a headline feature).
- **Acceptance:** full CLI flow (import → code → review 10 assignments → irr → export all) runs in CI with fake LLM; fidelity test green.

## Phase 6 — Methodology Packs MP-1 & MP-2 (Tasks 6.1–6.3)

- [ ] 6.1 Author `thematic_analysis` pack (Braun & Clarke 2006) with citation, decision questions, schema, 3 few-shots, golden fixture (expert-coded 12-segment transcript, reference kappa target ≥ 0.75).
- [ ] 6.2 Author `van_leeuwen` pack with the full taxonomy tree (exclusion, activation, passivation [subjection, beneficialisation], genericisation/specification, individualisation/assimilation, nomination, categorisation [identification, functionalisation, appraisement], determination/indetermination, differentiation, etc.), `actor_text` span requirement, 5 few-shots, golden fixture.
- [ ] 6.3 Golden fidelity tests for all three packs using recorded cassettes.
- **Acceptance:** `pytest tests/golden/` green; pack validation rejects a citation-less pack (negative test).

## Phase 7 — REST API (Tasks 7.1–7.2)

- [ ] 7.1 FastAPI app implementing every endpoint in 03_API_SPEC §3; routers thin over services; shared error handler mapping domain errors to the shared error object; OpenAPI at `/docs`.
- [ ] 7.2 Contract tests with httpx TestClient covering every endpoint, including error paths (404s, locked codebook, remote gate).
- **Acceptance:** `qualagent serve` boots; contract suite green; OpenAPI schema has no warnings.

## Phase 8 — MCP Server (Tasks 8.1–8.2)

- [ ] 8.1 `mcp_server/server.py` with FastMCP: all 9 tools + 2 resources from 03_API_SPEC §4.
- [ ] 8.2 Contract tests using the official MCP Python client over stdio.
- **Acceptance:** server connects from a real MCP client (test with Claude Desktop config example in docs); tool suite green.

## Phase 9 — Packaging, Docs & Release (Tasks 9.1–9.5)

- [ ] 9.1 Dockerfile (python:3.12-slim, non-root user, `ENTRYPOINT ["qualagent"]`, volume `/data`); docker-compose for demo.
- [ ] 9.2 MkDocs Material docs: quickstart, packs authoring guide, privacy/offline guide, API reference, MCP setup guide.
- [ ] 9.3 Finalize README per 06_README_DRAFT.md; record demo GIF (import → code → review → export with Ollama).
- [ ] 9.4 Submit to `awesome-qualitative-research` / relevant awesome lists; add topics on GitHub: `qualitative-research`, `thematic-analysis`, `nvivo-alternative`, `mcp-server`, `llm-agent`, `research-tools`.
- [ ] 9.5 Tag v1.0.0; PyPI release workflow (trusted publishing).
- **Acceptance:** fresh-machine test — `pip install qualagent`, run quickstart offline with Ollama, produce exports in < 10 minutes.

---

## Definition of Done (every task)

1. Code + tests committed; CI green (ruff, mypy, pytest).
2. Public functions documented (docstrings, Google style).
3. Relevant doc updated if behavior diverges from spec — update the spec doc in the same commit.
4. Audit events emitted for any state-changing operation.
