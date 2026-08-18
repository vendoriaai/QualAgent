# Product Requirements Document (PRD)
# QualAgent — Open-Source AI Agent for Qualitative Data Analysis

**Version:** 1.0
**Status:** Approved for build
**License:** MIT
**Repository name:** `qualagent`

---

## 1. Executive Summary

QualAgent is an open-source, Python-based AI agent that performs methodology-faithful qualitative data analysis (QDA) on interview transcripts and textual documents. Unlike generic "chat with your data" tools, QualAgent applies named, published qualitative frameworks (starting with Braun & Clarke's thematic analysis and van Leeuwen's social actor representation taxonomy) with a complete audit trail for every coding decision — the feature researchers need for defensible, publishable AI-assisted analysis.

## 2. Problem Statement

1. Commercial QDA software (NVivo, ATLAS.ti, MAXQDA) is expensive, closed-source, and has only shallow AI features with no auditability.
2. Generic LLM chatbots can "analyze" text but produce unverifiable, non-reproducible output with no trace from claim to source quote.
3. Academic journals increasingly require transparency about AI use in analysis; researchers currently have no open tool that generates a defensible audit trail.
4. No open-source tool encodes established qualitative methodologies as structured, inspectable agent workflows.

## 3. Goals (In Scope for v1.0)

- G1: Ingest interview transcripts and documents (TXT, DOCX, PDF, SRT, CSV).
- G2: Segment documents into codable units (sentences/utterances/paragraphs) with stable character offsets.
- G3: Apply pluggable methodology packs:
  - MP-1: Thematic analysis (Braun & Clarke 6-phase, adapted for agentic workflow).
  - MP-2: Van Leeuwen social actor representation taxonomy (full category tree).
  - MP-3: Open/descriptive coding (grounded-theory-lite initial coding).
- G4: Generate a draft codebook (code name, definition, inclusion/exclusion criteria, example quotes) and refine it iteratively.
- G5: Assign codes to segments with rationale, confidence score, and exact source offsets for every assignment.
- G6: Maintain an immutable, append-only audit trail of every agent action (model, prompt hash, input, output, timestamp, human override).
- G7: Human-in-the-loop review workflow: approve / reject / edit / reassign codes; all human actions also audited.
- G8: Compute inter-rater reliability (Cohen's kappa) between AI coding and human-verified coding.
- G9: Expose all capabilities as (a) a CLI, (b) a REST API, and (c) an MCP server so external AI assistants can use QualAgent as a tool.
- G10: Long-term project memory (vector store) so the agent remembers prior coding decisions, code definitions, and user corrections across sessions.
- G11: Export results: CSV/Excel code matrix, Markdown/PDF codebook, audit log export, and a methods-section paragraph draft describing the AI-assisted procedure.

## 4. Non-Goals (Out of Scope for v1.0)

- Audio/video transcription (assume text input; note Whisper integration as future work).
- Statistical/quantitative text analysis beyond IRR metrics.
- Real-time collaboration / multi-user editing (single-user, multi-project).
- Cloud-hosted SaaS; QualAgent is local-first/self-hosted.
- GUI beyond a minimal web review interface (CLI-first).
- Automatic report/paper writing beyond the methods-paragraph draft.

## 5. Personas

- P1: Doctoral researcher in applied linguistics/education. Needs defensible AI-assisted coding for a dissertation or journal article; must show supervisors/reviewers exactly how each code was assigned.
- P2: Academic supervisor/reviewer. Wants to inspect the audit trail and verify fidelity to the claimed methodology.
- P3: Developer/power user. Wants to embed QDA capabilities into their own AI workflows via API or MCP.

## 6. User Stories (priority order)

1. As P1, I can create a project and import transcripts so they are stored with stable IDs and offsets.
2. As P1, I can select a methodology pack so the agent codes according to a named framework.
3. As P1, I can run an initial coding pass and receive a draft codebook with definitions and examples.
4. As P1, I can review each code assignment, see the exact quote and the agent's rationale, and approve/reject/edit it.
5. As P1, my corrections are remembered and applied to subsequent coding of the same project.
6. As P1, I can compute Cohen's kappa between agent coding and my verified coding.
7. As P1, I can export a complete audit log suitable for an appendix or reviewer request.
8. As P1, I can export the codebook and a code×document matrix for reporting.
9. As P2, I can query the audit trail for any code assignment and see model, prompt, rationale, and offsets.
10. As P3, I can call QualAgent from Claude/any MCP client to code a segment or retrieve a codebook.
11. As P3, I can configure the LLM provider (OpenAI, Anthropic, Google, or local via Ollama) with a single config file.
12. As P1, I can run the entire tool offline with a local model for sensitive interview data (privacy-first).

## 7. Functional Requirements

- FR-1 Project management: create/list/delete projects; project = set of documents + codebook + coding state + audit log.
- FR-2 Ingestion: parse TXT/DOCX/PDF/SRT/CSV; normalize to plain text; store original file hash (SHA-256) for provenance.
- FR-3 Segmentation: deterministic, rule-based segmenters (utterance, sentence, paragraph, turn-taking for interview format with speaker labels); offsets computed once and immutable.
- FR-4 Methodology packs as versioned, declarative YAML/JSON definitions: category tree, coding instructions, decision questions, output schema. Packs are data, not code.
- FR-5 Coding engine: agent processes segments in batches; produces structured JSON output validated against the pack's schema; retries with corrective prompt on schema violation (max 2 retries, then flag segment).
- FR-6 Codebook lifecycle: draft → refined → locked. Locking a codebook version snapshots it; all subsequent assignments reference the locked version.
- FR-7 Audit trail: append-only event log; every LLM call and every human action recorded; exportable as JSONL and human-readable PDF.
- FR-8 Review queue: list/filter assignments by status (pending/approved/rejected/edited), confidence, code, document.
- FR-9 Memory: store user corrections and code definitions as embeddings; retrieval injected into coding prompts for consistency.
- FR-10 IRR: Cohen's kappa (per code and overall) between agent assignments and human-verified assignments on a user-selected subset.
- FR-11 Exports: code matrix CSV/XLSX, codebook MD/PDF, audit JSONL/PDF, methods-paragraph MD.
- FR-12 Interfaces: full-featured CLI (Typer), REST API (FastAPI, OpenAPI docs), MCP server (tools listed in API spec).
- FR-13 LLM abstraction: provider-agnostic via a single adapter interface; support OpenAI, Anthropic, Google Gemini, Ollama (local).
- FR-14 Configuration: single `qualagent.toml` per project + global config; secrets via environment variables only.

## 8. Non-Functional Requirements

- NFR-1 Local-first: runs fully offline with Ollama; no telemetry by default.
- NFR-2 Reproducibility: same document + same locked codebook + same model + same seed → same coding output; record model version and temperature in audit events.
- NFR-3 Performance: code a 10,000-word transcript in < 5 minutes with a hosted model on commodity hardware; stream progress.
- NFR-4 Data safety: never send data to a hosted LLM unless the configured provider is hosted; warn on first run if provider is remote.
- NFR-5 Testability: ≥ 80% line coverage on core modules; golden-dataset regression tests for each methodology pack.
- NFR-6 Code quality: Python 3.12+, type hints throughout, `ruff` lint + format, `mypy` strict on core modules.
- NFR-7 Packaging: installable via `pip install qualagent`; Docker image for one-command deployment.

## 9. Success Metrics

- Retrieval fidelity: 100% of exported quotes must map back to exact source offsets (automated test).
- Framework fidelity: golden-dataset agreement ≥ 0.75 kappa vs. expert-coded reference for each pack.
- Adoption: 500+ GitHub stars within 6 months of launch; 10+ external citations/uses.
- Latency: NFR-3 targets met in CI benchmark.

## 10. Milestones

- M1 (Week 1–2): Core domain model, storage, ingestion, segmentation, CLI skeleton.
- M2 (Week 3–4): LLM adapter, coding engine, MP-3 (open coding), audit trail.
- M3 (Week 5–6): Codebook lifecycle, review queue, memory, MP-1 (thematic analysis).
- M4 (Week 7–8): MP-2 (van Leeuwen), IRR metrics, exports, REST API.
- M5 (Week 9–10): MCP server, Docker, docs, README, demo GIF, v1.0 release.

## 11. Risks & Mitigations

- R1: LLM output inconsistency → structured output validation, temperature 0 default, seed pinning where supported, golden tests.
- R2: Framework misrepresentation → packs reviewed against primary sources; every pack includes a citation file and fidelity test.
- R3: Scope creep (GUI, transcription) → non-goals enforced; future-work section in docs.
- R4: Academic skepticism of AI coding → audit trail + kappa + methods-paragraph export are the core differentiators; document limitations honestly in README.

## 12. Future Work (post-v1.0)

- Whisper-based transcription; REFI-QDA (.qdpx) interchange; web review UI; multi-coder support; axial/selective coding pack; self-efficacy instrument pack; multilingual coding (Persian, German, Romanian).
