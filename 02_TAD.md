# Technical Architecture Document (TAD)
# QualAgent — System Design & Technical Decisions

**Version:** 1.0
**Companion to:** 01_PRD.md

---

## 1. Architecture Overview

QualAgent is a modular monolith with a clean core and three thin interface layers. All business logic lives in the core; CLI, REST API, and MCP server are adapters over the same application services.

```
┌──────────────────────────────────────────────────────────┐
│                    INTERFACE LAYER                        │
│   CLI (Typer)      REST API (FastAPI)     MCP Server     │
└──────────────┬───────────────┬────────────────┬──────────┘
               │               │                │
┌──────────────▼───────────────▼────────────────▼──────────┐
│                 APPLICATION SERVICES                      │
│  ProjectService  IngestionService  CodingService          │
│  ReviewService   CodebookService   ExportService          │
│  IRRService      AuditService                             │
├───────────────────────────────────────────────────────────┤
│                      DOMAIN CORE                          │
│  Entities (Project, Document, Segment, Code, Codebook,    │
│  Assignment, AuditEvent) · Methodology Pack engine ·      │
│  Coding orchestrator · Prompt builder · Schema validator  │
├───────────────────────────────────────────────────────────┤
│                  INFRASTRUCTURE                           │
│  LLM adapters (OpenAI/Anthropic/Gemini/Ollama)            │
│  SQLite (SQLModel) · ChromaDB (vectors) · File store      │
└───────────────────────────────────────────────────────────┘
```

## 2. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Target community; author's ecosystem |
| CLI | Typer + Rich | Fast to build, great UX, progress bars |
| REST API | FastAPI + Uvicorn | OpenAPI docs free; async |
| MCP server | `mcp` Python SDK (FastMCP) | Official SDK, minimal boilerplate |
| ORM / DB | SQLModel + SQLite (WAL mode) | Zero-config, single-file, typed models |
| Vector store | ChromaDB (embedded, persistent) | Local-first, no server needed |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (default, local) | Offline-capable; provider embeddings optional |
| LLM access | Thin internal adapter (no LangChain) | Full control of prompts, audit, retries; avoids dependency churn |
| Validation | Pydantic v2 | Structured LLM output schemas |
| Document parsing | python-docx, pypdf, csv stdlib, srt | Mature, lightweight |
| Exports | pandas + openpyxl (XLSX), Markdown + pandoc-free PDF via reportlab | No external binaries |
| IRR | scikit-learn `cohen_kappa_score` | Standard, tested |
| Testing | pytest, pytest-asyncio, respx (HTTP mocks) | Standard |
| Packaging | pyproject.toml (hatchling), Docker (python:3.12-slim) | pip-installable + one-command container |
| Quality | ruff, mypy (strict on core), pre-commit | Enforced in CI |

## 3. Repository Layout

```
qualagent/
├── pyproject.toml
├── README.md
├── qualagent/
│   ├── __init__.py
│   ├── config.py                # global + project config loading (TOML)
│   ├── domain/
│   │   ├── models.py            # SQLModel entities
│   │   ├── schemas.py           # Pydantic I/O schemas (LLM-facing)
│   │   └── errors.py
│   ├── packs/
│   │   ├── loader.py            # pack discovery, validation, versioning
│   │   ├── engine.py            # pack → prompt/decision-tree execution
│   │   └── builtin/
│   │       ├── open_coding/pack.yaml
│   │       ├── thematic_analysis/pack.yaml
│   │       └── van_leeuwen/pack.yaml
│   ├── services/
│   │   ├── project_service.py
│   │   ├── ingestion_service.py
│   │   ├── segmentation_service.py
│   │   ├── coding_service.py    # orchestrates coding runs
│   │   ├── codebook_service.py
│   │   ├── review_service.py
│   │   ├── memory_service.py
│   │   ├── irr_service.py
│   │   ├── export_service.py
│   │   └── audit_service.py
│   ├── llm/
│   │   ├── base.py              # LLMProvider protocol
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   ├── gemini_provider.py
│   │   ├── ollama_provider.py
│   │   └── structured.py        # JSON-schema-constrained call + retry
│   ├── storage/
│   │   ├── db.py                # engine, session, migrations (Alembic-lite)
│   │   └── files.py             # raw document store + hashing
│   ├── cli/
│   │   └── main.py              # Typer app, one command module per service
│   ├── api/
│   │   └── main.py              # FastAPI app, routers per service
│   └── mcp_server/
│       └── server.py            # FastMCP tools
├── tests/
│   ├── unit/
│   ├── integration/
│   └── golden/                  # golden datasets + fidelity tests per pack
├── docs/
└── docker/
    └── Dockerfile
```

## 4. Data Model (core entities)

- **Project**: id (uuid), name, created_at, config_json, active_pack, active_codebook_version.
- **Document**: id, project_id, filename, sha256, mime, raw_text, imported_at.
- **Segment**: id, document_id, index, start_offset, end_offset, text (derived), speaker (nullable), segmenter_version. Immutable after creation.
- **Codebook**: id, project_id, version, status (draft/refined/locked), locked_at.
- **Code**: id, codebook_id, parent_id (nullable, for taxonomies), name, definition, inclusion_criteria, exclusion_criteria, example_segment_ids.
- **Assignment**: id, segment_id, code_id, source (ai|human), rationale, confidence (0–1), status (pending/approved/rejected/edited), run_id, created_at.
- **CodingRun**: id, project_id, pack_version, codebook_version, model, temperature, seed, started_at, finished_at, stats_json.
- **AuditEvent**: id, project_id, ts, actor (ai|human|system), event_type, payload_json (prompt hash, model, inputs hash, outputs, overrides). Append-only; no UPDATE/DELETE permitted at the service layer.
- **MemoryItem**: id, project_id, kind (correction|definition|note), text, embedding (Chroma), created_at.

## 5. Key Design Decisions

- D1: **No agent framework.** Coding is a deterministic orchestration loop (segment batching → prompt → structured output → validate → persist → audit), not an autonomous agent loop. Predictability and reproducibility beat autonomy for research. The "agent" character comes from memory, tool use via MCP, and iterative codebook refinement.
- D2: **Packs are data.** A methodology pack is a versioned YAML with: metadata + citation, category tree, coding instructions, per-category decision questions, Pydantic output schema (as JSON Schema), and few-shot examples. Adding a framework = adding a folder, no code changes.
- D3: **Immutable segments.** Offsets computed once at ingestion with a versioned segmenter; re-segmentation creates new segments, never mutates.
- D4: **Append-only audit.** Enforced at service layer (no update/delete paths) and by SQLite trigger as defense in depth.
- D5: **Structured output with repair.** Every LLM call declares a JSON Schema; on parse/validation failure, one corrective retry with the validation error appended; second failure flags the segment `needs_human`.
- D6: **Determinism knobs.** Default temperature 0; seed recorded; model name+version recorded per run; golden tests replay fixed fixtures with a mock LLM.
- D7: **Memory as RAG over corrections.** User edits generate MemoryItems; coding prompts retrieve top-k (default 8) relevant memories + current code definitions.
- D8: **Privacy gate.** At first run and on provider change, if provider is remote, print a prominent warning listing data that will leave the machine; require `--accept-remote` or config flag.

## 6. Coding Pipeline (sequence)

1. `CodingService.start_run(project, pack, codebook)` → creates CodingRun, snapshots pack + codebook versions.
2. Load pending segments in batches (default 20 segments or ~4k tokens, whichever first).
3. Prompt builder assembles: pack instructions + category tree + code definitions + retrieved memories + batch segments (with IDs).
4. `llm.structured_call(schema=PackOutputSchema)` → validated JSON: per-segment code assignments + rationale + confidence + optional new-code proposals.
5. Persist assignments (status=pending); new-code proposals go to codebook as draft suggestions, never auto-applied.
6. Emit AuditEvent per batch (prompt hash, response hash, counts).
7. On completion, write run stats; segments that failed validation are queued for human review.

## 7. Interface Surface (summary)

- CLI: `qualagent init|import|segment|code|review|codebook|irr|export|serve|mcp|config` (full spec in 03_API_SPEC.md).
- REST: `/projects`, `/documents`, `/segments`, `/codebooks`, `/codes`, `/assignments`, `/runs`, `/review`, `/irr`, `/exports`, `/audit` (full spec in 03_API_SPEC.md).
- MCP tools: `list_projects`, `get_codebook`, `code_segment`, `search_segments`, `get_assignments`, `submit_human_decision`, `get_audit_trail`, `compute_irr`.

## 8. Security & Privacy

- Secrets only via env vars (`QUALAGENT_OPENAI_API_KEY`, etc.); never written to disk or audit log (payloads are hashed, not stored raw, when provider is remote — configurable).
- Local mode (Ollama + local embeddings) is fully offline; CI includes a no-network test for local mode.
- Docker image runs as non-root; volume-mounts project directory.

## 9. Testing Strategy

- Unit: services with in-memory SQLite + fake LLM provider (scripted responses).
- Integration: CLI end-to-end flows (import → code → review → export) with fake LLM.
- Golden: per pack, a small expert-coded fixture transcript; assert kappa ≥ 0.75 against reference using a recorded LLM cassette (vcr-style) so tests are deterministic.
- Contract: MCP tools tested with the official MCP client; REST tested via httpx TestClient.
- CI: GitHub Actions — lint, mypy, tests, benchmark (NFR-3), Docker build.

## 10. Deployment & Distribution

- `pip install qualagent` (PyPI) — primary.
- `docker run -v ./data:/data qualagent/qualagent` — secondary.
- Docs: MkDocs Material on GitHub Pages; README with demo GIF, badges, quickstart (< 5 commands to first coded transcript).
