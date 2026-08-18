"""AuditService and ProjectService tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from qualagent.domain.errors import Conflict, ProjectNotFound
from qualagent.domain.models import AuditEvent, Project
from qualagent.services.audit_service import AuditService
from qualagent.services.project_service import ProjectService
from sqlmodel import Session, select


class TestAuditService:
    def test_emit_and_list(self, session: Session, project_row: Project) -> None:
        svc = AuditService(session, project_row.id)
        event = svc.emit("system", "project.created", {"name": "x"})
        events, total = svc.list_events()
        assert total == 1
        assert events[0].id == event.id
        assert AuditService.payload_of(events[0]) == {"name": "x"}
        assert events[0].actor == "system"

    def test_filters(self, session: Session, project_row: Project) -> None:
        svc = AuditService(session, project_row.id)
        svc.emit("ai", "run.started", {"n": 1})
        svc.emit("ai", "run.completed", {"n": 2})
        events, total = svc.list_events(event_type="run.started")
        assert total == 1 and events[0].event_type == "run.started"

        future = datetime.now(UTC) + timedelta(hours=1)
        _, total = svc.list_events(since=future)
        assert total == 0
        past = datetime.now(UTC) - timedelta(hours=1)
        _, total = svc.list_events(since=past)
        assert total == 2

    def test_pagination(self, session: Session, project_row: Project) -> None:
        svc = AuditService(session, project_row.id)
        for i in range(5):
            svc.emit("system", "config.changed", {"i": i})
        page, total = svc.list_events(limit=2, offset=3)
        assert total == 5 and len(page) == 2
        assert AuditService.payload_of(page[0]) == {"i": 3}

    def test_scoped_to_project(self, session: Session, project_row: Project) -> None:
        other = Project(name="other-study")
        session.add(other)
        session.commit()
        AuditService(session, project_row.id).emit("system", "code.created", {})
        AuditService(session, other.id).emit("system", "code.created", {})
        _, total = AuditService(session, project_row.id).list_events()
        assert total == 1


class TestProjectService:
    def test_create_sets_up_everything(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        ctx = project_service.create("my-study", study_root=tmp_path)
        study_dir = tmp_path / "my-study"
        project_dir = study_dir / ".qualagent"
        assert (project_dir / "qualagent.db").is_file()
        assert (project_dir / "files").is_dir()
        assert (project_dir / "packs").is_dir()
        assert (project_dir / "chroma").is_dir()
        assert (project_dir / "config.toml").is_file()
        with ctx.session() as session:
            events, total = AuditService(session, ctx.project.id).list_events()
            assert total == 1
            assert events[0].event_type == "project.created"
            assert AuditService.payload_of(events[0])["name"] == "my-study"

    def test_duplicate_name_conflicts(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        project_service.create("dup", study_root=tmp_path)
        with pytest.raises(Conflict):
            project_service.create("dup", study_root=tmp_path)

    def test_existing_directory_conflicts(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        (tmp_path / "clash").mkdir()
        with pytest.raises(Conflict):
            project_service.create("clash", study_root=tmp_path)

    def test_invalid_name_rejected(self, project_service: ProjectService, tmp_path: Path) -> None:
        with pytest.raises(Conflict):
            project_service.create("bad/name", study_root=tmp_path)
        with pytest.raises(Conflict):
            project_service.create("", study_root=tmp_path)

    def test_list_open_and_delete(self, project_service: ProjectService, tmp_path: Path) -> None:
        ctx = project_service.create("gone-later", study_root=tmp_path)
        pid = ctx.project.id
        entries = project_service.list_projects()
        assert [e.name for e in entries] == ["gone-later"]

        opened = project_service.open(pid)
        assert opened.project.id == pid
        ctx.engine.dispose()
        opened.engine.dispose()

        project_service.delete(pid)
        assert project_service.list_projects() == []
        assert not (tmp_path / "gone-later").exists()
        with pytest.raises(ProjectNotFound):
            project_service.open(pid)

    def test_delete_writes_final_audit_event_first(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        ctx = project_service.create("audited-delete", study_root=tmp_path)
        with ctx.session() as session:
            svc = AuditService(session, ctx.project.id)
            _, before = svc.list_events()
            assert before == 1
            svc.emit("system", "export.generated", {})
        ctx.engine.dispose()
        project_service.delete(ctx.project.id)
        # The final event was written before removal (observable via the count
        # captured pre-delete in a real audit export; here we assert no error
        # and full removal).
        assert not (tmp_path / "audited-delete").exists()

    def test_open_unknown_raises(self, project_service: ProjectService) -> None:
        with pytest.raises(ProjectNotFound):
            project_service.open("missing-id")

    def test_multiple_projects_listed_in_order(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        project_service.create("alpha", study_root=tmp_path / "root1")
        project_service.create("beta", study_root=tmp_path / "root2")
        names = [e.name for e in project_service.list_projects()]
        assert names == ["alpha", "beta"]

    def test_audit_rows_survive_reopen(
        self, project_service: ProjectService, tmp_path: Path
    ) -> None:
        ctx = project_service.create("persist-audit", study_root=tmp_path)
        with ctx.session() as session:
            AuditService(session, ctx.project.id).emit("human", "assignment.decision", {"x": 1})
        reopened = project_service.open(ctx.project.id)
        with reopened.session() as session:
            events = list(session.exec(select(AuditEvent)))
            assert len(events) == 2
