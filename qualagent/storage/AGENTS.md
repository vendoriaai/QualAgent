# qualagent.storage — Database and File Storage

## Purpose

Persistence layer: SQLite database (SQLModel) and local file storage for documents, exports, and pack artifacts.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- `db.py`: `Database` class — SQLModel engine, session management, `create_all()`, `drop_all()`
  - Connection string via `Config.database_url` (default: `sqlite:///./qualagent.db`)
  - Provides `session()` context manager
- `files.py`: `FileStorage` class — local filesystem operations
  - Base path via `Config.storage_path` (default: `./storage/`)
  - Methods: `save()`, `load()`, `delete()`, `list()`, `exists()`
- `registry.py`: `PackRegistry` — discovers and caches loaded packs
  - Scans `Config.packs_dir` (default: `./packs/`) + builtin packs

## Work Guidance

- Services access DB via `Database.session()` — never create engines directly
- FileStorage handles all file I/O — no raw `open()` in services
- PackRegistry used by `packs.loader` for pack discovery
- Migrations: not yet implemented (dev DB recreated on schema change)

## Verification

- `pytest tests/unit/test_storage.py`
- `pytest tests/unit/test_models_db.py`

## Child DOX Index

- No child AGENTS.md files needed