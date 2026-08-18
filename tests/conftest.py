"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from qualagent.domain.models import Project
from qualagent.services.project_service import ProjectService
from qualagent.storage.db import init_db, make_engine
from qualagent.storage.registry import ProjectRegistry
from sqlalchemy.engine import Engine
from sqlmodel import Session


@pytest.fixture
def global_dir(tmp_path: Path) -> Path:
    """Isolated global config directory for a test."""
    gdir = tmp_path / "home" / ".qualagent"
    gdir.mkdir(parents=True)
    return gdir


@pytest.fixture
def db_engine(tmp_path: Path) -> Generator[Engine]:
    """Fresh initialized database engine (schema + audit triggers)."""
    engine = make_engine(tmp_path / "test.qualagent.db")
    init_db(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(db_engine: Engine) -> Generator[Session]:
    """A session on the test database, rolled back after each test."""
    with Session(db_engine) as sess:
        yield sess
        sess.rollback()


@pytest.fixture
def project_row(session: Session) -> Project:
    """A persisted Project row."""
    project = Project(name="test-study")
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    """Path to an isolated project registry file."""
    return tmp_path / "registry.json"


@pytest.fixture
def project_service(registry_path: Path) -> ProjectService:
    """ProjectService backed by an isolated registry."""
    return ProjectService(ProjectRegistry(registry_path))
