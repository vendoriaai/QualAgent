# qualagent.services — Business Logic Services

## Purpose

Core business logic layer: project lifecycle, ingestion, segmentation, coding pipeline, codebook management, review, IRR, export, audit, and memory.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package — used by API, CLI, and pack engine

## Local Contracts

- All services instantiated via `qualagent.runtime.Runtime` (single instance per process)
- Services:
  - `ProjectService` — CRUD for projects
  - `IngestionService` — document import, text extraction
  - `SegmentationService` — split documents into segments
  - `CodingService` — apply codes to segments (LLM + human)
  - `CodebookService` — codebook CRUD, versioning, hierarchy
  - `ReviewService` — human review workflow
  - `IRRService` — inter-rater reliability (Cohen's Kappa, Fleiss' Kappa)
  - `ExportService` — CSV, JSON, Excel, CAQDAS formats
  - `AuditService` — immutable audit log
  - `MemoryService` — working memory for pack execution
- Each service uses `qualagent.storage.db.Database` for persistence
- Domain errors from `qualagent.domain.errors`

## Work Guidance

- Services are stateless — all state in DB/files
- Add new service: create class, register in `Runtime.__init__`
- Keep services focused — one responsibility each
- Use `MemoryService` for pack-step context, not service-to-service calls
- Unit tests in `tests/unit/test_services.py` and per-service test files

## Verification

- `pytest tests/unit/test_services.py`
- `pytest tests/unit/test_coding_pipeline.py`
- `pytest tests/unit/test_review.py`
- `pytest tests/unit/test_irr_export.py`
- `pytest tests/integration/test_cli_*.py`

## Child DOX Index

- No child AGENTS.md files needed