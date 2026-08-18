"""Project lifecycle: create, list, open, delete studies.

Each study owns an independent ``.qualagent`` directory (database, files,
packs, vector store). Cross-project discovery goes through the machine-level
registry (ADR-004).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from qualagent.domain.errors import Conflict, ProjectNotFound
from qualagent.domain.models import Project
from qualagent.runtime import (
    ProjectContext,
    create_study_layout,
    init_project,
    open_project,
)
from qualagent.services.audit_service import AuditService
from qualagent.storage.registry import ProjectRegistry, RegistryEntry


class ProjectService:
    """Manage studies via the registry and per-study databases."""

    def __init__(self, registry: ProjectRegistry) -> None:
        self._registry = registry

    def create(
        self,
        name: str,
        *,
        study_root: Path,
        pack: str | None = None,
    ) -> ProjectContext:
        """Create a new study directory with database and audit trail.

        Args:
            name: Study name; the study directory is ``study_root / name``.
            study_root: Parent directory for the study folder.
            pack: Optional active methodology pack ("name@version").

        Returns:
            The opened ProjectContext for the new study.

        Raises:
            Conflict: If the name is registered or the directory exists.
        """
        if not name or name.strip() != name or "/" in name or "\\" in name:
            raise Conflict(f"Invalid project name: {name!r}")
        if self._registry.find_by_name(name) is not None:
            raise Conflict(f"A project named {name!r} already exists")
        study_dir = study_root / name
        if study_dir.exists():
            raise Conflict(f"Directory {study_dir} already exists")

        project_dir = create_study_layout(study_dir)
        project = init_project(project_dir, name=name, pack=pack)
        self._registry.register(project.id, project.name, study_dir)

        ctx = open_project(project_dir)
        with ctx.session() as session:
            audit = AuditService(session, project.id)
            audit.emit(
                "system",
                "project.created",
                {"name": name, "pack": pack, "project_dir": str(project_dir)},
            )
        return ctx

    def list_projects(self) -> list[RegistryEntry]:
        """List all registered projects."""
        return self._registry.list_all()

    def open(self, project_id: str) -> ProjectContext:
        """Open a registered project by id.

        Raises:
            ProjectNotFound: If the id is not registered or the data is gone.
        """
        entry = self._registry.get(project_id)
        return open_project(Path(entry.path) / ".qualagent")

    def get_project_row(self, project_id: str) -> Project:
        """Return the Project entity for a registered project."""
        ctx = self.open(project_id)
        with ctx.session() as session:
            project = session.get(Project, ctx.project.id)
            if project is None:  # pragma: no cover - defensive
                raise ProjectNotFound(f"Project row missing for {project_id}")
            return project

    def delete(self, project_id: str) -> None:
        """Delete a study: final audit event, registry removal, directory removal.

        Raises:
            ProjectNotFound: If the id is not registered.
        """
        ctx = self.open(project_id)
        with ctx.session() as session:
            audit = AuditService(session, ctx.project.id)
            audit.emit(
                "human",
                "project.deleted",
                {"name": ctx.project.name, "reason": "user-requested delete"},
            )
        ctx.engine.dispose()
        self._registry.unregister(project_id)
        study_dir = Path(ctx.project_dir).parent
        # Windows can hold a brief lock on WAL files after dispose; retry.
        for attempt in range(3):
            try:
                shutil.rmtree(study_dir)
                return
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.2)
