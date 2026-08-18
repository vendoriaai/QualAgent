# API & Interface Specification
# QualAgent — CLI, REST API, and MCP Server Contracts

**Version:** 1.0 · **Companion to:** 01_PRD.md, 02_TAD.md

All three interfaces call the same application services. Behavior, validation, and error semantics are identical across interfaces.

---

## 1. Shared Conventions

- IDs: UUIDv4 strings.
- Errors: structured object `{ "error": { "code": "STRING_CODE", "message": "...", "details": {...} } }`.
- Error codes: `PROJECT_NOT_FOUND`, `DOCUMENT_NOT_FOUND`, `SEGMENT_NOT_FOUND`, `CODEBOOK_LOCKED`, `INVALID_PACK`, `LLM_VALIDATION_FAILED`, `REMOTE_PROVIDER_NOT_ACCEPTED`, `CONFLICT`, `VALIDATION_ERROR`.
- Pagination (REST): `?limit=50&offset=0`, response envelope `{ "items": [...], "total": N }`.
- Timestamps: ISO-8601 UTC.

---

## 2. CLI Specification (Typer)

Binary name: `qualagent`. Global flags: `--project-dir PATH` (default `./.qualagent`), `--json` (machine-readable output), `--verbose`.

| Command | Args / Options | Description | Exit codes |
|---|---|---|---|
| `qualagent init NAME` | `--pack NAME` | Create project, config, DB, dirs | 0 ok, 2 exists |
| `qualagent import PATH...` | `--project ID` | Ingest files; prints document IDs + segment counts | 0 ok, 3 parse error |
| `qualagent segment DOC_ID` | `--strategy utterance\|sentence\|paragraph\|turns` | Re-run segmentation (creates new segments) | 0 ok |
| `qualagent code run` | `--pack NAME --codebook VER --batch-size 20 --accept-remote` | Execute coding run; streams progress | 0 ok, 4 LLM failure, 5 remote not accepted |
| `qualagent code status RUN_ID` | | Run progress/stats | 0 ok |
| `qualagent review list` | `--status pending --code NAME --min-confidence 0.0 --max-confidence 1.0` | List assignments for review | 0 ok |
| `qualagent review decide ASSIGNMENT_ID` | `--approve \| --reject \| --edit-code CODE_NAME --note TEXT` | Record human decision; writes memory on edit | 0 ok |
| `qualagent codebook show` | `--version N` | Print codebook (MD) | 0 ok |
| `qualagent codebook refine` | `--from-suggestions` | Agent-assisted codebook refinement pass | 0 ok |
| `qualagent codebook lock` | `--version N` | Lock codebook version | 0 ok, 6 already locked |
| `qualagent irr compute` | `--subset verified` | Cohen's kappa AI vs human, per-code and overall | 0 ok |
| `qualagent export matrix` | `--format csv\|xlsx --out PATH` | Code × document matrix | 0 ok |
| `qualagent export codebook` | `--format md\|pdf --out PATH` | Codebook document | 0 ok |
| `qualagent export audit` | `--format jsonl\|pdf --out PATH` | Full audit trail | 0 ok |
| `qualagent export methods` | `--out PATH` | Draft methods-section paragraph (MD) | 0 ok |
| `qualagent serve` | `--host 127.0.0.1 --port 8741` | Start REST API | 0 ok |
| `qualagent mcp` | `--transport stdio\|sse` | Start MCP server | 0 ok |
| `qualagent config show/set` | | View/edit project config | 0 ok |
| `qualagent packs list/show` | | List builtin + custom packs; show pack detail | 0 ok |

---

## 3. REST API Specification (FastAPI)

Base: `/api/v1`. Full OpenAPI generated at `/docs`. Request/response bodies are the Pydantic schemas from `domain/schemas.py` (see 04_DATA_MODEL.md).

### Projects
| Method & Path | Body | Response | Notes |
|---|---|---|---|
| POST `/projects` | `{name, pack?, config?}` | `Project` | 409 if name exists |
| GET `/projects` | — | `Project[]` (paged) | |
| GET `/projects/{id}` | — | `Project` | 404 |
| DELETE `/projects/{id}` | — | 204 | Cascades; writes final audit event first |

### Documents & Segments
| Method & Path | Body | Response |
|---|---|---|
| POST `/projects/{id}/documents` | multipart file upload | `Document` + `segment_count` |
| GET `/projects/{id}/documents` | — | paged list |
| GET `/documents/{id}/segments` | `?limit&offset` | `Segment[]` |
| POST `/documents/{id}/resegment` | `{strategy}` | `{created: N}` |

### Coding
| Method & Path | Body | Response |
|---|---|---|
| POST `/projects/{id}/runs` | `{pack, codebook_version?, batch_size?}` | `CodingRun` (async; poll status) |
| GET `/runs/{id}` | — | `CodingRun` with stats |
| GET `/projects/{id}/assignments` | filters: `status, code, confidence_min/max, document_id` | paged `Assignment[]` |
| POST `/assignments/{id}/decision` | `{action: approve\|reject\|edit, code_id?, note?}` | updated `Assignment` |

### Codebook
| Method & Path | Body | Response |
|---|---|---|
| GET `/projects/{id}/codebooks` | — | `Codebook[]` |
| GET `/codebooks/{id}` | — | `Codebook` + `Code[]` tree |
| POST `/codebooks/{id}/refine` | `{from_suggestions: bool}` | `Codebook` (new draft version) |
| POST `/codebooks/{id}/lock` | — | `Codebook` (status=locked) |
| POST `/codebooks/{id}/codes` | `Code` | `Code` | manual code creation |

### Analysis & Exports
| Method & Path | Body | Response |
|---|---|---|
| GET `/projects/{id}/irr` | `?subset=verified` | `{overall_kappa, per_code: {name: kappa}}` |
| POST `/projects/{id}/exports` | `{kind: matrix\|codebook\|audit\|methods, format}` | file download |
| GET `/projects/{id}/audit` | `?event_type&since` | paged `AuditEvent[]` |

---

## 4. MCP Server Specification

Server name: `qualagent`. Transports: stdio (default), SSE. Tools return JSON; errors map to MCP error responses with the shared codes.

| Tool | Input Schema | Output | Description |
|---|---|---|---|
| `list_projects` | `{}` | `Project[]` | All projects |
| `get_codebook` | `{project_id, version?}` | codebook tree as JSON | Current or specified version |
| `code_segment` | `{project_id, text, speaker?, pack?}` | `{assignments: [{code, rationale, confidence}]}` | Ad-hoc coding of one text snippet (not persisted as document; audited) |
| `search_segments` | `{project_id, query, k?}` | `Segment[]` with scores | Semantic search over segments |
| `get_assignments` | `{project_id, status?, code?}` | `Assignment[]` | Filtered assignments |
| `submit_human_decision` | `{assignment_id, action, code_id?, note?}` | `Assignment` | Approve/reject/edit |
| `get_audit_trail` | `{project_id, event_type?, since?}` | `AuditEvent[]` | Audit query |
| `compute_irr` | `{project_id}` | kappa report | AI vs human agreement |
| `propose_codes` | `{project_id, document_id?}` | `Code[]` suggestions | Draft new codes from uncoded/flagged segments |

MCP resources: `qualagent://{project_id}/codebook` (JSON), `qualagent://{project_id}/audit` (JSONL stream).

---

## 5. LLM Structured Output Contract (internal)

Every coding call must return JSON matching the active pack's output schema. Common envelope:

```json
{
  "results": [
    {
      "segment_id": "uuid",
      "assignments": [
        {"code": "activation", "rationale": "...", "confidence": 0.86}
      ],
      "uncodable": false
    }
  ],
  "new_code_proposals": [
    {"name": "...", "definition": "...", "example_segment_id": "uuid"}
  ]
}
```

Rules: `rationale` must cite the segment text; `confidence` in [0,1]; multi-code allowed if pack permits (`multi_code: true` in pack YAML); `uncodable: true` flags segment for human review.
