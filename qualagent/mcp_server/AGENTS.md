# qualagent.mcp_server — MCP Server

## Purpose

Exposes QualAgent as Model Context Protocol tools and resources so MCP hosts
(e.g. Claude Desktop) can drive studies programmatically (03_API_SPEC section 4).

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Entry point: `qualagent.mcp_server.server.main(transport="stdio")` (CLI: `qualagent mcp`)
- Runnable as a subprocess: `python -m qualagent.mcp_server` (stdio, default transport)
- Server name: `qualagent`; transports: stdio (default), SSE
- Thin handlers over the same application services the CLI and REST API use
  (`ProjectService`, `CodebookService`, `CodingService`, `ReviewService`,
  `IRRCalculator`, `AuditService`, `SegmentSearchService`); no business logic
  lives here
- Domain errors map to MCP protocol errors: a `QualAgentError` is converted to
  an `MCPError` whose `data.code` carries the shared string error code
  (03_API_SPEC section 1) so error semantics are identical across CLI/REST/MCP
- Tools (9): `list_projects`, `get_codebook`, `code_segment`, `search_segments`,
  `get_assignments`, `submit_human_decision`, `get_audit_trail`, `compute_irr`,
  `propose_codes`
- Resources (2, URI templates): `qualagent://{project_id}/codebook` (JSON),
  `qualagent://{project_id}/audit` (JSONL, one event per line)
- `get_codebook` and the codebook resource return an empty tree
  (`status == "empty"`) for a project with no codebook yet, rather than an error
- Test seam: `QUALAGENT_FAKE_CASSETTE` selects a `FakeProvider` (fully offline),
  mirroring the CLI's `--llm fake --cassette`; `QUALAGENT_FAKE_EMBEDDINGS`
  selects a deterministic bag-of-words embedder for `search_segments` (no ONNX load)

## Work Guidance

- Add a tool by decorating a function with `@mcp_server.tool(...)` + `@_map_errors`
  and delegating to a service; never inline business logic
- Keep handlers thin and mirror the REST/CLI behavior for the same operation
- New tools must have a contract test in `tests/integration/test_mcp_contract.py`
- Projects are opened from the machine-level registry on demand (same pattern as
  `qualagent/api/app.py`); use `_using_project(project_id)` or `_ctx_for_row(...)`

## Verification

- `pytest tests/integration/test_mcp_contract.py` — stdio contract suite
  (official MCP Python client over stdio; 17 tests covering all 9 tools,
  2 resources, and error paths)
- `python -m qualagent.mcp_server` (stdio) connects from a real MCP client

## Child DOX Index

- No child AGENTS.md files needed
