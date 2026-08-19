"""REST API contract tests via httpx TestClient (03_API_SPEC section 3)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from qualagent.api.app import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    studies = tmp_path / "studies"
    studies.mkdir()
    monkeypatch.setenv("QUALAGENT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(studies)
    return TestClient(app)


class TestProjects:
    def test_create_list_get_delete(self, client: TestClient) -> None:
        created = client.post(
            "/api/v1/projects", json={"name": "api-study", "pack": "open_coding@1.0.0"}
        )
        assert created.status_code == 201, created.text
        project = created.json()
        assert project["name"] == "api-study"
        pid = project["id"]

        listed = client.get("/api/v1/projects")
        assert listed.status_code == 200
        assert listed.json()["total"] >= 1

        got = client.get(f"/api/v1/projects/{pid}")
        assert got.status_code == 200 and got.json()["id"] == pid

        missing = client.get("/api/v1/projects/nope")
        assert missing.status_code == 404

        deleted = client.delete(f"/api/v1/projects/{pid}")
        assert deleted.status_code == 204

    def test_duplicate_name_conflict(self, client: TestClient) -> None:
        client.post("/api/v1/projects", json={"name": "dupe"})
        second = client.post("/api/v1/projects", json={"name": "dupe"})
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "CONFLICT"


class TestDocuments:
    def test_upload_list_segments_resegment(self, client: TestClient) -> None:
        pid = client.post("/api/v1/projects", json={"name": "docs"}).json()["id"]
        upload = client.post(
            f"/api/v1/projects/{pid}/documents",
            files={
                "file": (
                    "interview.txt",
                    io.BytesIO(b"First sentence here.\nSecond one."),
                    "text/plain",
                )
            },
            params={"strategy": "sentence"},
        )
        assert upload.status_code == 201, upload.text
        doc = upload.json()
        assert doc["filename"] == "interview.txt"
        assert doc["segment_count"] == 2

        listed = client.get(f"/api/v1/projects/{pid}/documents")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        segments = client.get(f"/api/v1/documents/{doc['id']}/segments")
        assert segments.status_code == 200
        segs = segments.json()["items"]
        assert len(segs) == 2
        assert segs[0]["text"] == "First sentence here."
        # Offset invariant through the API.
        assert segs[0]["start_offset"] == 0

        reseg = client.post(
            f"/api/v1/documents/{doc['id']}/resegment", params={"strategy": "paragraph"}
        )
        assert reseg.status_code == 200

        missing = client.get("/api/v1/documents/ghost/segments")
        assert missing.status_code == 404


class TestCodebook:
    def test_lifecycle(self, client: TestClient) -> None:
        pid = client.post("/api/v1/projects", json={"name": "cb"}).json()["id"]
        books = client.get(f"/api/v1/projects/{pid}/codebooks")
        assert books.status_code == 200
        # No codebook until a run creates one; create manually via refine 404 first.
        assert books.json()["total"] == 0

        missing = client.get("/api/v1/codebooks/ghost")
        assert missing.status_code == 404

    def test_codebook_refine_lock_flow(self, client: TestClient) -> None:
        pid = client.post("/api/v1/projects", json={"name": "cb2"}).json()["id"]
        # Seed a codebook through the service layer.
        from qualagent.config import global_config_dir
        from qualagent.services.codebook_service import CodebookService
        from qualagent.services.project_service import ProjectService
        from qualagent.storage.registry import ProjectRegistry

        svc = ProjectService(ProjectRegistry(global_config_dir() / "registry.json"))
        ctx = svc.open(pid)
        with ctx.session() as session:
            books = CodebookService(session, _audit(session, pid))
            book = books.ensure_codebook(ctx.project)
            books.add_code(book, name="seed_code", definition="A seed")
            book_id = book.id
        ctx.engine.dispose()

        refined = client.post(f"/api/v1/codebooks/{book_id}/refine")
        assert refined.status_code == 200, refined.text
        assert refined.json()["status"] == "refined"
        assert refined.json()["version"] == 2

        added = client.post(
            f"/api/v1/codebooks/{refined.json()['id']}/codes",
            json={"name": "manual_code", "definition": "Human-added"},
        )
        assert added.status_code == 201, added.text

        locked = client.post(f"/api/v1/codebooks/{refined.json()['id']}/lock")
        assert locked.status_code == 200
        assert locked.json()["status"] == "locked"

        # Locked codebook rejects new codes with 409 CODEBOOK_LOCKED.
        blocked = client.post(
            f"/api/v1/codebooks/{refined.json()['id']}/codes",
            json={"name": "too_late"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "CODEBOOK_LOCKED"


def _audit(session, pid):
    from qualagent.services.audit_service import AuditService

    return AuditService(session, pid)


class TestReviewAndIrr:
    def test_assignments_decision_irr_audit(self, client: TestClient) -> None:
        pid, assignment_id = _seed_assignments(client)
        listed = client.get(f"/api/v1/projects/{pid}/assignments", params={"status": "pending"})
        assert listed.status_code == 200
        assert listed.json()["total"] == 2

        decision = client.post(
            f"/api/v1/assignments/{assignment_id}/decision",
            json={"action": "approve", "note": "looks right"},
        )
        assert decision.status_code == 200, decision.text
        assert decision.json()["status"] == "approved"

        # Double decision -> CONFLICT 409.
        again = client.post(
            f"/api/v1/assignments/{assignment_id}/decision",
            json={"action": "reject"},
        )
        assert again.status_code == 409

        irr = client.get(f"/api/v1/projects/{pid}/irr")
        assert irr.status_code == 200
        assert irr.json()["n_compared"] == 1

        audit = client.get(f"/api/v1/projects/{pid}/audit")
        assert audit.status_code == 200
        types = {e["event_type"] for e in audit.json()["items"]}
        assert "assignment.approved" in types


def _seed_assignments(client: TestClient) -> tuple[str, str]:
    """Create project + document + pending assignments directly."""
    pid = client.post("/api/v1/projects", json={"name": "rev"}).json()["id"]

    from qualagent.config import global_config_dir
    from qualagent.domain.models import Assignment, Code, Codebook
    from qualagent.services.project_service import ProjectService
    from qualagent.storage.registry import ProjectRegistry

    svc = ProjectService(ProjectRegistry(global_config_dir() / "registry.json"))
    ctx = svc.open(pid)
    with ctx.session() as session:
        upload = client.post(
            f"/api/v1/projects/{pid}/documents",
            files={"file": ("t.txt", io.BytesIO(b"One two three.\nFour five."), "text/plain")},
        )
        doc = upload.json()
        segments = client.get(f"/api/v1/documents/{doc['id']}/segments").json()["items"]
        book = Codebook(project_id=pid, version=1, status="draft")
        session.add(book)
        session.commit()
        c = Code(codebook_id=book.id, name="s_code")
        session.add(c)
        session.commit()
        a1 = Assignment(segment_id=segments[0]["id"], code_id=c.id, source="ai", confidence=0.9)
        a2 = Assignment(segment_id=segments[1]["id"], code_id=c.id, source="ai", confidence=0.5)
        session.add_all([a1, a2])
        session.commit()
        aid = a1.id
    ctx.engine.dispose()
    return pid, aid


class TestExports:
    def test_methods_and_audit_export(self, client: TestClient) -> None:
        pid = client.post("/api/v1/projects", json={"name": "exp"}).json()["id"]
        methods = client.post(
            f"/api/v1/projects/{pid}/exports",
            params={"kind": "methods", "format": "md"},
        )
        assert methods.status_code == 200
        assert b"QualAgent" in methods.content

        audit = client.post(
            f"/api/v1/projects/{pid}/exports",
            params={"kind": "audit", "format": "jsonl"},
        )
        assert audit.status_code == 200

        bad = client.post(
            f"/api/v1/projects/{pid}/exports",
            params={"kind": "nope", "format": "md"},
        )
        assert bad.status_code == 422


class TestOpenApi:
    def test_docs_served(self, client: TestClient) -> None:
        docs = client.get("/docs")
        assert docs.status_code == 200
        spec = client.get("/openapi.json")
        assert spec.status_code == 200
        paths = spec.json()["paths"]
        for expected in (
            "/api/v1/projects",
            "/api/v1/projects/{project_id}/documents",
            "/api/v1/documents/{document_id}/segments",
            "/api/v1/projects/{project_id}/runs",
            "/api/v1/assignments/{assignment_id}/decision",
            "/api/v1/codebooks/{codebook_id}/lock",
            "/api/v1/projects/{project_id}/irr",
            "/api/v1/projects/{project_id}/exports",
            "/api/v1/projects/{project_id}/audit",
        ):
            assert expected in paths, f"missing {expected}"
