"""Pack discovery, validation, and versioning.

A pack is a folder containing ``pack.yaml``. Discovery covers the built-in
packs shipped with QualAgent (``qualagent/packs/builtin/``) and project packs
(``{study}/.qualagent/packs/``), with project packs overriding built-ins of the
same name (closer-to-the-work wins).

Validation rules (04_DATA_MODEL section 3, builder rule 5):
- required metadata: name (snake_case), semantic version, title, citation
  (packs without a citation are rejected — hard requirement)
- category tree is a mapping of mappings, finite depth, acyclic by construction
- ``output_schema`` is a valid JSON Schema object
- at least one few-shot example is strongly recommended but not required
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from qualagent.domain.errors import InvalidPack, ValidationError

BUILTIN_DIR = Path(__file__).parent / "builtin"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

REQUIRED_KEYS = ("name", "version", "title", "citation", "coding_instructions")
ALLOWED_MULTI_CODE = (True, False)


@dataclass(frozen=True)
class Pack:
    """A validated methodology pack."""

    name: str
    version: str
    title: str
    citation: str
    multi_code: bool
    unit: str
    category_tree: dict[str, Any]
    coding_instructions: str
    decision_questions: list[str]
    output_schema: dict[str, Any]
    few_shot_examples: list[dict[str, Any]]
    origin: str  # "builtin" | "project"
    path: Path

    @property
    def id(self) -> str:
        """Stable pack identifier ``name@version``."""
        return f"{self.name}@{self.version}"

    def category_paths(self) -> list[str]:
        """Flatten the category tree to dotted paths (e.g. ``exclusion.suppression``)."""
        paths: list[str] = []

        def walk(node: dict[str, Any], prefix: str) -> None:
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                paths.append(path)
                if isinstance(value, dict):
                    children = value.get("children")
                    if isinstance(children, dict):
                        walk(children, path)

        walk(self.category_tree, "")
        return paths

    def category_descriptions(self) -> dict[str, str]:
        """Map dotted category path -> description."""
        descriptions: dict[str, str] = {}

        def walk(node: dict[str, Any], prefix: str) -> None:
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict):
                    desc = value.get("description", "")
                    if isinstance(desc, str) and desc:
                        descriptions[path] = desc
                    children = value.get("children")
                    if isinstance(children, dict):
                        walk(children, path)
                elif isinstance(value, dict):  # pragma: no cover
                    walk(value, path)

        walk(self.category_tree, "")
        return descriptions


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InvalidPack(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidPack(f"{path}: pack file must be a YAML mapping")
    return data


def validate_pack_data(data: dict[str, Any], origin: str, path: Path) -> None:
    """Raise :class:`InvalidPack` unless the pack data satisfies all rules."""
    for key in REQUIRED_KEYS:
        if key not in data:
            raise InvalidPack(f"{path}: missing required key '{key}'")
    if not _NAME_RE.match(str(data["name"])):
        raise InvalidPack(f"{path}: pack name must be snake_case, got {data['name']!r}")
    if not _VERSION_RE.match(str(data["version"])):
        raise InvalidPack(f"{path}: version must be semver X.Y.Z, got {data['version']!r}")
    if not str(data["citation"]).strip():
        raise InvalidPack(f"{path}: citation is required (packs must be citable)")
    if data.get("multi_code") not in ALLOWED_MULTI_CODE:
        raise InvalidPack(f"{path}: multi_code must be true or false")
    if data.get("unit", "segment") != "segment":
        raise InvalidPack(f"{path}: only unit 'segment' is supported in v1")

    tree = data.get("category_tree")
    if not isinstance(tree, dict) or not tree:
        raise InvalidPack(f"{path}: category_tree must be a non-empty mapping")
    _check_tree(tree, path, depth=0)

    schema = data.get("output_schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise InvalidPack(f"{path}: output_schema must be a JSON Schema object")

    questions = data.get("decision_questions", [])
    if not isinstance(questions, list) or not all(isinstance(q, str) for q in questions):
        raise InvalidPack(f"{path}: decision_questions must be a list of strings")

    few_shots = data.get("few_shot_examples", [])
    if not isinstance(few_shots, list) or not all(isinstance(fs, dict) for fs in few_shots):
        raise InvalidPack(f"{path}: few_shot_examples must be a list of mappings")


def _check_tree(node: dict[str, Any], path: Path, depth: int) -> None:
    if depth > 4:
        raise InvalidPack(f"{path}: category tree deeper than 4 levels")
    for key, value in node.items():
        if not _NAME_RE.match(str(key)):
            raise InvalidPack(f"{path}: category names must be snake_case, got {key!r}")
        if isinstance(value, dict):
            children = value.get("children")
            if children is not None:
                if not isinstance(children, dict):
                    raise InvalidPack(f"{path}: children of '{key}' must be a mapping")
                _check_tree(children, path, depth + 1)
        elif value is not None and not isinstance(value, (str, dict)):
            raise InvalidPack(f"{path}: category '{key}' value must be a mapping")


def parse_pack(data: dict[str, Any], *, origin: str, path: Path) -> Pack:
    """Validate and build a :class:`Pack` from raw YAML data."""
    validate_pack_data(data, origin, path)
    return Pack(
        name=str(data["name"]),
        version=str(data["version"]),
        title=str(data["title"]),
        citation=str(data["citation"]),
        multi_code=bool(data["multi_code"]),
        unit=str(data.get("unit", "segment")),
        category_tree=dict(data["category_tree"]),
        coding_instructions=str(data["coding_instructions"]),
        decision_questions=[str(q) for q in data.get("decision_questions", [])],
        output_schema=dict(data["output_schema"]),
        few_shot_examples=list(data.get("few_shot_examples", [])),
        origin=origin,
        path=path,
    )


def load_pack_from_dir(pack_dir: Path, *, origin: str) -> Pack:
    """Load and validate the pack inside ``pack_dir`` (must contain pack.yaml)."""
    pack_file = pack_dir / "pack.yaml"
    if not pack_file.is_file():
        raise InvalidPack(f"{pack_dir}: no pack.yaml found")
    return parse_pack(_load_yaml(pack_file), origin=origin, path=pack_file)


def _discover_in(root: Path, origin: str) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and (entry / "pack.yaml").is_file():
            data = _load_yaml(entry / "pack.yaml")
            name = str(data.get("name", entry.name))
            found[name] = entry
    return found


def discover_packs(project_packs_dir: Path | None = None) -> dict[str, Path]:
    """Return pack name -> directory, project packs overriding built-ins."""
    packs = _discover_in(BUILTIN_DIR, "builtin")
    if project_packs_dir is not None:
        packs.update(_discover_in(project_packs_dir, "project"))
    return packs


def load_pack(
    name: str,
    *,
    project_packs_dir: Path | None = None,
) -> Pack:
    """Load a pack by name (``name`` or ``name@version``).

    Raises:
        InvalidPack: If the pack is unknown or fails validation.
        ValidationError: If a pinned version does not match the pack file.
    """
    pure_name, _, pinned_version = name.partition("@")
    dirs = discover_packs(project_packs_dir)
    if pure_name not in dirs:
        available = ", ".join(sorted(dirs)) or "none"
        raise InvalidPack(f"Unknown pack '{pure_name}'. Available packs: {available}")
    pack = load_pack_from_dir(
        dirs[pure_name],
        origin="project"
        if pure_name in (_discover_in(project_packs_dir, "project") if project_packs_dir else {})
        else "builtin",
    )
    if pinned_version and pinned_version != pack.version:
        raise ValidationError(
            f"Pack '{pure_name}' is version {pack.version}, but {name} was requested"
        )
    return pack
