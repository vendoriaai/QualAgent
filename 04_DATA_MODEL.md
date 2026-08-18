# Data Model & Methodology Pack Specification
# QualAgent — Schemas, Database DDL, and Pack Format

**Version:** 1.0 · **Companion to:** 01_PRD.md, 02_TAD.md

---

## 1. SQLModel Entities (authoritative field lists)

```python
class Project(SQLModel, table=True):
    id: str = Field(primary_key=True)          # uuid4
    name: str = Field(index=True, unique=True)
    created_at: datetime
    config_json: str                            # project-level config overrides
    active_pack: str | None                     # e.g. "van_leeuwen@1.0.0"
    active_codebook_version: int | None

class Document(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    filename: str
    sha256: str                                 # provenance hash of original file
    mime: str
    raw_text: str                               # normalized plain text
    imported_at: datetime

class Segment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    document_id: str = Field(foreign_key="document.id", index=True)
    index: int                                  # order within document
    start_offset: int
    end_offset: int
    speaker: str | None                         # for interview turns
    segmenter_version: str                      # e.g. "turns@1"
    # text is ALWAYS derived: document.raw_text[start_offset:end_offset]
    # UNIQUE(document_id, index); segments are immutable

class Codebook(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    version: int                                # per-project monotonic
    status: str                                 # draft | refined | locked
    locked_at: datetime | None
    # UNIQUE(project_id, version)

class Code(SQLModel, table=True):
    id: str = Field(primary_key=True)
    codebook_id: str = Field(foreign_key="codebook.id", index=True)
    parent_id: str | None = Field(foreign_key="code.id")
    name: str                                   # snake_case, unique per codebook
    definition: str
    inclusion_criteria: str
    exclusion_criteria: str
    example_segment_ids: str                    # JSON array of segment ids

class CodingRun(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    pack: str                                   # "name@version"
    codebook_version: int
    model: str                                  # e.g. "gpt-5.2-2026-06-01" or "ollama/qwen3:32b"
    temperature: float = 0.0
    seed: int | None
    status: str                                 # running | completed | failed
    started_at: datetime
    finished_at: datetime | None
    stats_json: str                             # {segments_coded, flagged, retries, tokens}

class Assignment(SQLModel, table=True):
    id: str = Field(primary_key=True)
    segment_id: str = Field(foreign_key="segment.id", index=True)
    code_id: str = Field(foreign_key="code.id")
    run_id: str | None = Field(foreign_key="codingrun.id")  # null for human-created
    source: str                                 # ai | human
    rationale: str
    confidence: float | None                    # null for human
    status: str = "pending"                     # pending | approved | rejected | edited
    created_at: datetime
    # INDEX(segment_id, status)

class AuditEvent(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    ts: datetime
    actor: str                                  # ai | human | system
    event_type: str                             # see taxonomy below
    payload_json: str                           # hashed prompts when provider is remote
    # APPEND-ONLY: services expose create + read only; SQLite trigger blocks UPDATE/DELETE

class MemoryItem(SQLModel, table=True):
    id: str = Field(primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    kind: str                                   # correction | definition | note
    text: str
    chroma_id: str                              # vector store pointer
    created_at: datetime
```

### Audit event_type taxonomy
`project.created`, `document.imported`, `document.resegmented`, `run.started`, `run.batch_completed`, `run.completed`, `run.failed`, `assignment.created`, `assignment.decision`, `codebook.created`, `codebook.refined`, `codebook.locked`, `code.created`, `code.updated`, `export.generated`, `llm.call` (prompt_hash, response_hash, model, tokens), `config.changed`.

### Append-only enforcement (SQLite trigger)

```sql
CREATE TRIGGER audit_no_update BEFORE UPDATE ON auditevent
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON auditevent
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
```

## 2. Pydantic Schemas (API + LLM I/O)

Define in `domain/schemas.py`: `ProjectIn/Out`, `DocumentOut`, `SegmentOut`, `CodeIn/Out`, `CodebookOut`, `AssignmentOut`, `DecisionIn`, `RunIn/Out`, `IRRReport`, `CodingBatchResult` (LLM envelope from 03_API_SPEC §5). All LLM-facing schemas use `model_config = ConfigDict(extra="forbid")` so hallucinated fields fail validation.

## 3. Methodology Pack Format (pack.yaml)

```yaml
name: van_leeuwen
version: 1.0.0
title: "Van Leeuwen Social Actor Representation"
citation: "van Leeuwen, T. (2008). Discourse and Power: Representations of Social Actors. Palgrave Macmillan."
multi_code: true
unit: segment
category_tree:
  exclusion:                       # top-level categories
    description: "Social actor omitted or backgrounded"
    children:
      suppression: {description: "No reference to the actor anywhere in the text"}
      backgrounding: {description: "Actor mentioned elsewhere but absent from this clause"}
  inclusion:
    children:
      activation: {description: "Actor represented as active doer (agent of material process)"}
      passivation:
        children:
          subjection: {description: "Actor affected by the action"}
          beneficialisation: {description: "Actor benefits from the action"}
      genericisation: {description: "Actor as class/group rather than specific individual"}
      # ... full taxonomy continues: nomination, categorization (identification,
      # functionalization, appraisement), individualization, assimilation, etc.
coding_instructions: |
  You are coding interview text using van Leeuwen's (2008) social actor
  representation taxonomy. For each segment: (1) identify every social actor
  referenced; (2) for each actor, decide inclusion/exclusion first, then apply
  the decision questions; (3) assign the most specific applicable category...
decision_questions:
  - "Is the actor present in this segment? If not → exclusion."
  - "Is the actor the grammatical/semantic agent of the main process? If yes → activation."
  - "Is the actor referred to by name or unique identity? If yes → nomination."
output_schema:                      # JSON Schema for structured LLM output
  type: object
  required: [results]
  properties:
    results:
      type: array
      items:
        type: object
        required: [segment_id, assignments]
        properties:
          segment_id: {type: string}
          assignments:
            type: array
            items:
              type: object
              required: [code, rationale, confidence]
              properties:
                code: {type: string}
                actor_text: {type: string}     # exact span being coded
                rationale: {type: string}
                confidence: {type: number, minimum: 0, maximum: 1}
few_shot_examples:
  - input: "The teacher explained the grammar point to the class."
    output: [{code: "activation", actor_text: "The teacher", rationale: "Actor is agent of material process 'explained'", confidence: 0.95}]
fidelity_test: golden/van_leeuwen_fixture.json
```

### Built-in packs to ship in v1.0
1. `open_coding@1.0.0` — descriptive/initial coding; free-form code proposals; `multi_code: true`.
2. `thematic_analysis@1.0.0` — Braun & Clarke (2006) adapted: Phase 2 initial codes → Phase 3 theme grouping (separate `refine` pass) → Phase 4 review via human queue; citation included.
3. `van_leeuwen@1.0.0` — full taxonomy tree per the 2008 monograph; `actor_text` span required in output.

### Pack loader rules
- Discover packs in `qualagent/packs/builtin/` and `{project}/.qualagent/packs/`.
- Validate on load: YAML schema, category tree acyclic, output_schema valid JSON Schema, citation present (hard requirement — packs without citations are rejected).
- Version pinning: runs record `name@version`; changing a pack file requires bumping its version.

## 4. Configuration Files

`~/.qualagent/config.toml` (global) and `{project}/.qualagent/config.toml` (overrides):

```toml
[llm]
provider = "ollama"            # openai | anthropic | gemini | ollama
model = "qwen3:32b"
temperature = 0.0
seed = 42
max_retries = 2

[llm.remote]
accepted = false               # must be true (or --accept-remote) to use hosted APIs

[memory]
embedding_model = "all-MiniLM-L6-v2"
top_k = 8

[coding]
batch_size = 20
batch_token_budget = 4000
```

Environment variables: `QUALAGENT_OPENAI_API_KEY`, `QUALAGENT_ANTHROPIC_API_KEY`, `QUALAGENT_GOOGLE_API_KEY`, `QUALAGENT_OLLAMA_HOST`.

## 5. Export Formats

- **Code matrix (CSV/XLSX)**: rows = codes (with hierarchy flattened `parent.child`), columns = documents, cells = frequency of approved+pending assignments; extra sheet with per-segment detail.
- **Codebook (MD/PDF)**: per code — name, definition, inclusion/exclusion criteria, 2 example quotes with document + offsets.
- **Audit (JSONL/PDF)**: JSONL is the raw event stream; PDF is a human-readable rendering grouped by run.
- **Methods paragraph (MD)**: template filling pack citation, model, temperature, date range, segment counts, kappa, and a human-oversight statement — designed to be edited into a paper's methodology section.
