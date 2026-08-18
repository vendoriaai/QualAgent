"""Runtime helpers: study layout creation and opening an existing project."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from qualagent.config import QualAgentConfig, load_config
from qualagent.domain.errors import ProjectNotFound
from qualagent.domain.models import Project
from qualagent.storage.db import init_db, make_engine, project_db_path, session_factory_for


@dataclass
class ProjectContext:
    """An opened study: database engine, session factory, and config."""

    project_dir: Path
    engine: Engine
    session_factory: Callable[[], Session]
    config: QualAgentConfig
    project: Project

    def session(self) -> Session:
        """Return a new session bound to the study database."""
        return self.session_factory()


def create_study_layout(study_dir: Path) -> Path:
    """Create the ``.qualagent`` directory tree for a new study.

    Returns the project (``.qualagent``) directory.
    """
    project_dir = study_dir / ".qualagent"
    (project_dir / "files").mkdir(parents=True, exist_ok=True)
    (project_dir / "packs").mkdir(exist_ok=True)
    (project_dir / "chroma").mkdir(exist_ok=True)
    config_path = project_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(
            "# QualAgent project configuration. Overrides ~/.qualagent/config.toml.\n",
            encoding="utf-8",
        )
    return project_dir


def init_project(project_dir: Path, *, name: str, pack: str | None = None) -> Project:
    """Create the database, schema, triggers, and the single Project row.

    The temporary engine used here is disposed before returning; callers open
    their own engine via :func:`open_project`.
    """
    engine = make_engine(project_db_path(project_dir))
    init_db(engine)
    try:
        with Session(engine) as session:
            project = Project(name=name, active_pack=pack)
            session.add(project)
            session.commit()
            session.refresh(project)
            return project
    finally:
        engine.dispose()


def open_project(project_dir: Path) -> ProjectContext:
    """Open an existing study by its ``.qualagent`` directory.

    Raises:
        ProjectNotFound: If the directory has no project database or row.
    """
    db_path = project_db_path(project_dir)
    if not db_path.is_file():
        raise ProjectNotFound(f"No QualAgent project found at {project_dir}")
    engine = make_engine(db_path)
    config = load_config(project_dir)
    with Session(engine) as session:
        project = session.exec(select(Project)).first()
        if project is None:
            raise ProjectNotFound(f"Project database at {db_path} has no project row")
    return ProjectContext(
        project_dir=project_dir,
        engine=engine,
        session_factory=session_factory_for(engine),
        config=config,
        project=project,
    )
