"""Per-project SQLite engine, schema creation, and append-only audit triggers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

AUDIT_TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS audit_no_update
    BEFORE UPDATE ON auditevent
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS audit_no_delete
    BEFORE DELETE ON auditevent
    BEGIN
        SELECT RAISE(ABORT, 'audit log is append-only');
    END
    """,
]


def project_db_path(project_dir: Path) -> Path:
    """Return the SQLite database path inside a study ``.qualagent`` directory."""
    return project_dir / "qualagent.db"


def make_engine(db_path: Path) -> Engine:
    """Create a SQLite engine with WAL mode and foreign keys enabled."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create all tables and install the append-only audit triggers."""
    # Import for model registration side effects.
    from qualagent.domain import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        for trigger_ddl in AUDIT_TRIGGERS:
            conn.execute(text(trigger_ddl))


def session_factory_for(engine: Engine) -> Callable[[], Session]:
    """Return a session factory bound to the engine."""
    import functools

    return functools.partial(Session, engine)
