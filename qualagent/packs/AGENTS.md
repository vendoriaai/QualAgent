# qualagent.packs — Pack Engine and Builtin Packs

## Purpose

Pluggable methodology packs (open coding, thematic analysis, van Leeuwen) defining coding workflows, prompts, and schemas.

## Ownership

- Primary: QualAgent development team
- Part of `qualagent` package

## Local Contracts

- Pack spec: `pack.yaml` with `id`, `version`, `name`, `description`, `steps[]`, `schemas`
- Loader: `qualagent.packs.loader.load_pack(path)` → `Pack` object
- Engine: `qualagent.packs.engine.PackEngine` executes steps with LLM + memory
- Builtin packs in `qualagent/packs/builtin/`:
  - `open_coding/` — inductive open coding
  - `thematic_analysis/` — Braun & Clarke thematic analysis
  - `van_leeuwen/` — Van Leeuwen's discourse analysis
- Custom packs: drop `pack.yaml` in configured pack directory

## Work Guidance

- Define pack steps with `prompt_template`, `input_schema`, `output_schema`
- Use `PackEngine.run_step(pack, step_id, context)` for execution
- Memory service (`qualagent.services.memory_service`) provides context across steps
- Golden tests in `tests/golden/test_packs_golden.py` verify pack outputs
- Version packs semantically — breaking changes require major version bump

## Verification

- `pytest tests/unit/test_packs.py`
- `pytest tests/golden/test_packs_golden.py`

## Child DOX Index

- `builtin/open_coding/` — Open coding pack (pack.yaml only)
- `builtin/thematic_analysis/` — Thematic analysis pack (pack.yaml only)
- `builtin/van_leeuwen/` — Van Leeuwen pack (pack.yaml only)