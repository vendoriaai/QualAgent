"""MCP server contract tests over stdio (Phase 8, task 8.2).

Uses the official MCP Python client (``mcp.client.session.ClientSession``) over
stdio, exactly as a host would. Studies live in an isolated registry, the LLM is
canned through the fake-cassette test seam, and segment search is faked through a
deterministic bag-of-words embedder so the whole suite runs fully offline and
fast (no ONNX model load).

Each test is a sync function that runs exactly one ``asyncio.run`` over its case
coroutine. Keeping the stdio connection's setup, body, and teardown inside one
task is required: anyio cancel scopes (which ``stdio_client`` uses) cannot cross
tasks, and pytest-asyncio manages fixtures in separate tasks.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from qualagent.domain.models import Assignment, Code, Codebook, Document, Segment
from qualagent.services.project_service import ProjectService
from qualagent.storage.registry import ProjectRegistry

#: All 9 MCP tools the contract suite asserts are registered (03_API_SPEC section 4).
EXPECTED_TOOLS: set[str] = {
    "list_projects",
    "get_codebook",
    "code_segment",
    "search_segments",
    "get_assignments",
    "submit_human_decision",
    "get_audit_trail",
    "compute_irr",
    "propose_codes",
}
EXPECTED_RESOURCES: set[str] = {"qualagent://{project_id}/codebook", "qualagent://{project_id}/audit"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_cassette(tmp_path: Path) -> Path:
    """A cassette the code_segment/propose_codes tools consume (open_coding)."""
    response = json.dumps(
        {
            "results": [
                {
                    "segment_id": "ad-hoc",
                    "assignments": [
                        {"code": "activation", "rationale": "the actor acts upon others", "confidence": 0.9}
                    ],
                    "uncodable": False,
                }
            ],
            "new_code_proposals": [
                {"name": "community_seeking", "definition": "a new code", "example_segment_id": "ad-hoc"}
            ],
        }
    )
    path = tmp_path / "cassette.yaml"
    path.write_text(
        json.dumps(
            {
                "model": "fake/test",
                "responses": [
                    {"match": {"contains": "Code the following segments"}, "response": response},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def mcp_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Isolated QUALAGENT_HOME + fake seam env vars for the MCP subprocess."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("QUALAGENT_HOME", str(home))
    monkeypatch.setenv("QUALAGENT_FAKE_EMBEDDINGS", "1")
    monkeypatch.setenv("QUALAGENT_FAKE_CASSETTE", str(_write_cassette(tmp_path)))
    return dict(os.environ)


@asynccontextmanager
async def _mcp(env: dict[str, str]) -> Any:
    """Spawn the MCP server over stdio; yield an initialized client session.

    Setup, body, and teardown run in one task (the caller's coroutine under a
    single ``asyncio.run``), so anyio's cancel scopes stay valid.
    """
    params = StdioServerParameters(
        command=sys.executable,
        args=["-W", "ignore", "-m", "qualagent.mcp_server"],
        env=env,
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        yield s


async def _call(session: ClientSession, name: str, args: dict[str, Any]) -> Any:
    """Invoke a tool and return its decoded result.

    Prefers ``structured_content`` (the canonical MCP structured output). The
    FastMCP kernel wraps a bare ``list``/array return under a ``result`` key, so
    that wrapper is unwrapped; object returns are passed through. Falls back to
    the text content for tools that do not declare structured output.
    """
    result = await session.call_tool(name, args)
    assert not result.is_error, f"{name} returned error: {result.content}"
    sc = getattr(result, "structured_content", None)
    if isinstance(sc, dict) and isinstance(sc.get("result"), (list, dict)):
        return sc["result"]
    if isinstance(sc, (list, dict)):
        return sc
    return json.loads(result.content[0].text)


async def _read_resource(session: ClientSession, uri: str) -> str:
    """Read a resource's text content."""
    result = await session.read_resource(uri)
    return result.contents[0].text


def _make_study(home: Path, study_root: Path) -> dict[str, str]:
    """Create + seed a registered study with a document, codebook, and two codes."""
    svc = ProjectService(ProjectRegistry(home / "registry.json"))
    ctx = svc.create("mcp-study", study_root=study_root)
    pid = ctx.project.id
    try:
        with ctx.session() as s:
            doc = Document(
                project_id=pid,
                filename="interview.txt",
                sha256="0" * 64,
                mime="text/plain",
                raw_text="The activists mobilized the community.\nDoubts lingered overnight.",
            )
            s.add(doc)
            s.commit()
            s.add_all([
                Segment(document_id=doc.id, index=0, start_offset=0, end_offset=38,
                       segmenter_version="sentence@1"),
                Segment(document_id=doc.id, index=1, start_offset=39, end_offset=69,
                       segmenter_version="sentence@1"),
            ])
            s.commit()
            book = Codebook(project_id=pid, version=1, status="draft")
            s.add(book)
            s.commit()
            code_a = Code(codebook_id=book.id, name="activation", definition="acting upon others")
            s.add(code_a)
            s.add(Code(codebook_id=book.id, name="passivity", definition="acted upon"))
            s.commit()
            return {
                "project_id": pid,
                "document_id": doc.id,
                "codebook_id": book.id,
                "activation_code_id": code_a.id,
            }
    finally:
        ctx.engine.dispose()


def _seed_pending_assignment(study: dict[str, str], *, status: str, home: Path) -> str:
    """Create one pending AI assignment tied to the study's first segment."""
    svc = ProjectService(ProjectRegistry(home / "registry.json"))
    ctx = svc.open(study["project_id"])
    try:
        with ctx.session() as s:
            from sqlmodel import select

            seg = s.exec(select(Segment).where(Segment.document_id == study["document_id"])).first()
            assignment = Assignment(
                segment_id=seg.id,
                code_id=study["activation_code_id"],
                source="ai",
                rationale="AI-coded",
                confidence=0.8,
                status=status,
            )
            s.add(assignment)
            s.commit()
            return assignment.id
    finally:
        ctx.engine.dispose()


def _run(coro: Callable[[], Awaitable[Any]]) -> Any:
    """Run a case coroutine in its own event loop and return its result.

    Closing the loop after teardown keeps the stdio connection's anyio task
    groups fully cancelled, so no cancel scope leaks into the next test.
    """
    return asyncio.run(coro())


def _expect_mcp_error(
    case: Callable[[], Awaitable[Any]],
) -> MCPError:
    """Run a case expected to raise an MCPError; unwrap anyio's ExceptionGroup.

    anyio's task group re-raises tool failures inside nested ``ExceptionGroup``
    instances; this unwraps down to the first :class:`MCPError`.
    """
    caught: list[BaseException] = []
    try:
        asyncio.run(case())
    except* MCPError as group:
        caught.extend(group.exceptions)
    if not caught:
        raise AssertionError("expected an MCPError, none was raised")
    # Walk nested ExceptionGroups to the first MCPError leaf.
    queue = list(caught)
    while queue:
        item = queue.pop(0)
        if isinstance(item, MCPError):
            return item
        if isinstance(item, BaseExceptionGroup):
            queue.extend(item.exceptions)
    return caught[0]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Contract tests (one server connection per test, stdio transport)
# ---------------------------------------------------------------------------


class TestServerContract:
    def test_lists_all_tools(self, mcp_env: dict[str, str]) -> None:
        async def case() -> set[str]:
            async with _mcp(mcp_env) as s:
                return {t.name for t in (await s.list_tools()).tools}

        assert _run(case) >= EXPECTED_TOOLS

    def test_lists_resource_templates(self, mcp_env: dict[str, str]) -> None:
        async def case() -> set[str]:
            async with _mcp(mcp_env) as s:
                return {
                    tpl.uri_template
                    for tpl in (await s.list_resource_templates()).resource_templates
                }

        assert _run(case) >= EXPECTED_RESOURCES


class TestProjectsAndCodebook:
    def test_list_projects(self, mcp_env: dict[str, str], tmp_path: Path) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                return await _call(s, "list_projects", {})

        projects = _run(case)
        assert any(p["id"] == study["project_id"] for p in projects)

    def test_get_codebook_returns_tree(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> dict[str, Any]:
            async with _mcp(mcp_env) as s:
                return await _call(s, "get_codebook", {"project_id": study["project_id"]})

        cb = _run(case)
        assert cb["status"] in {"draft", "empty"}
        assert any(c["name"] == "activation" for c in cb["codes"])

    def test_get_codebook_unknown_version_raises(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> None:
            async with _mcp(mcp_env) as s:
                await s.call_tool(
                    "get_codebook", {"project_id": study["project_id"], "version": 999}
                )

        err = _expect_mcp_error(case)
        assert err.data["code"] in {"VALIDATION_ERROR", "CODEBOOK_LOCKED"}

    def test_project_not_found_surfaces_shared_code(self, mcp_env: dict[str, str]) -> None:
        async def case() -> None:
            async with _mcp(mcp_env) as s:
                await s.call_tool("get_codebook", {"project_id": "nope"})

        err = _expect_mcp_error(case)
        assert err.data["code"] == "PROJECT_NOT_FOUND"


class TestCodeSegment:
    def test_code_segment_returns_assignments(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> dict[str, Any]:
            async with _mcp(mcp_env) as s:
                return await _call(
                    s,
                    "code_segment",
                    {
                        "project_id": study["project_id"],
                        "text": "The activists mobilized the community.",
                        "pack": "open_coding",
                    },
                )

        out = _run(case)
        assert isinstance(out["assignments"], list)
        assert out["assignments"][0]["code"] == "activation"

    def test_code_segment_not_persisted(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        """Ad-hoc coding must not create a document or persisted assignment."""
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                await _call(
                    s,
                    "code_segment",
                    {"project_id": study["project_id"], "text": "Probe snippet.", "pack": "open_coding"},
                )
                return await _call(s, "get_audit_trail", {"project_id": study["project_id"]})

        trail = _run(case)
        types = {e["event_type"] for e in trail}
        assert "document.imported" not in types
        assert "code_segment.adhoc" in types


class TestSearchSegments:
    def test_search_ranks_relevant_segments_first(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                return await _call(
                    s,
                    "search_segments",
                    {"project_id": study["project_id"], "query": "mobilization by activists", "k": 5},
                )

        matches = _run(case)
        assert [m["index"] for m in matches] == [0, 1]
        assert matches[0]["score"] >= matches[1]["score"]
        assert matches[0]["text"] == "The activists mobilized the community."

    def test_empty_project_search_returns_empty(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        home = Path(mcp_env["QUALAGENT_HOME"])
        svc = ProjectService(ProjectRegistry(home / "registry.json"))
        ctx = svc.create("empty-study", study_root=tmp_path / "empty-study")
        pid = ctx.project.id
        ctx.engine.dispose()

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                return await _call(s, "search_segments", {"project_id": pid, "query": "anything"})

        assert _run(case) == []


class TestAssignmentsAndDecisions:
    def test_submit_decision_approve_and_irr(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        home = Path(mcp_env["QUALAGENT_HOME"])
        study = _make_study(home, tmp_path / "study")
        aid = _seed_pending_assignment(study, status="pending", home=home)

        async def case() -> tuple[dict[str, Any], dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                decided = await _call(
                    s,
                    "submit_human_decision",
                    {"assignment_id": aid, "action": "approve", "note": "ok"},
                )
                irr = await _call(s, "compute_irr", {"project_id": study["project_id"]})
                return decided, irr

        decided, irr = _run(case)
        assert decided["status"] == "approved"
        assert irr["n_compared"] == 1
        assert 0.0 <= irr["overall_kappa"] <= 1.0

    def test_invalid_action_raises_validation_error(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        home = Path(mcp_env["QUALAGENT_HOME"])
        study = _make_study(home, tmp_path / "study")
        aid = _seed_pending_assignment(study, status="pending", home=home)

        async def case() -> None:
            async with _mcp(mcp_env) as s:
                await s.call_tool(
                    "submit_human_decision", {"assignment_id": aid, "action": "bogus"}
                )

        err = _expect_mcp_error(case)
        assert err.data["code"] == "VALIDATION_ERROR"


class TestAudit:
    def test_audit_trail_and_resource(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> tuple[list[dict[str, Any]], str]:
            async with _mcp(mcp_env) as s:
                trail = await _call(s, "get_audit_trail", {"project_id": study["project_id"]})
                audit_text = await _read_resource(s, f"qualagent://{study['project_id']}/audit")
                return trail, audit_text

        trail, audit_text = _run(case)
        assert any(e["event_type"] == "project.created" for e in trail)
        # The audit resource is JSONL (one event per line).
        lines = audit_text.strip().splitlines()
        assert lines
        assert "event_type" in json.loads(lines[0])

    def test_audit_trail_filter_by_event_type(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                return await _call(
                    s,
                    "get_audit_trail",
                    {"project_id": study["project_id"], "event_type": "project.created"},
                )

        trail = _run(case)
        assert all(e["event_type"] == "project.created" for e in trail)
        assert len(trail) == 1


class TestProposeCodes:
    def test_propose_codes_returns_suggestions(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> list[dict[str, Any]]:
            async with _mcp(mcp_env) as s:
                return await _call(s, "propose_codes", {"project_id": study["project_id"]})

        proposals = _run(case)
        assert isinstance(proposals, list)
        if proposals:
            assert proposals[0]["name"] == "community_seeking"

    def test_propose_codes_not_persisted(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> dict[str, Any]:
            async with _mcp(mcp_env) as s:
                await _call(s, "propose_codes", {"project_id": study["project_id"]})
                return await _call(s, "get_codebook", {"project_id": study["project_id"]})

        cb = _run(case)
        # community_seeking stays a suggestion (suggestion_*) — never auto-added.
        assert "community_seeking" not in {c["name"] for c in cb["codes"]}


class TestResources:
    def test_codebook_resource(
        self, mcp_env: dict[str, str], tmp_path: Path
    ) -> None:
        study = _make_study(Path(mcp_env["QUALAGENT_HOME"]), tmp_path / "study")

        async def case() -> str:
            async with _mcp(mcp_env) as s:
                return await _read_resource(s, f"qualagent://{study['project_id']}/codebook")

        tree = json.loads(_run(case))
        assert tree["status"] in {"draft", "empty"}
        assert any(c["name"] == "activation" for c in tree["codes"])
