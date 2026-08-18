"""Pack loader and engine tests, including the citation-less negative test."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from qualagent.domain.errors import InvalidPack, ValidationError
from qualagent.packs.engine import PackEngine
from qualagent.packs.loader import (
    BUILTIN_DIR,
    discover_packs,
    load_pack,
    load_pack_from_dir,
    parse_pack,
)

OPEN_CODING_YAML = (BUILTIN_DIR / "open_coding" / "pack.yaml").read_text(encoding="utf-8")


def _pack_data() -> dict:
    return yaml.safe_load(OPEN_CODING_YAML)


class TestBuiltinPack:
    def test_load_open_coding(self) -> None:
        pack = load_pack("open_coding")
        assert pack.id == "open_coding@1.0.0"
        assert pack.multi_code is True
        assert "Saldana" in pack.citation
        assert pack.origin == "builtin"

    def test_load_with_pinned_version(self) -> None:
        assert load_pack("open_coding@1.0.0").name == "open_coding"

    def test_wrong_pin_rejected(self) -> None:
        with pytest.raises(ValidationError):
            load_pack("open_coding@9.9.9")

    def test_unknown_pack_lists_available(self) -> None:
        with pytest.raises(InvalidPack, match="Available packs"):
            load_pack("nope")


class TestValidation:
    def test_missing_citation_rejected(self) -> None:
        data = _pack_data()
        del data["citation"]
        with pytest.raises(InvalidPack, match="citation"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_empty_citation_rejected(self) -> None:
        data = _pack_data()
        data["citation"] = "   "
        with pytest.raises(InvalidPack, match="citation"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_bad_name_rejected(self) -> None:
        data = _pack_data()
        data["name"] = "Not-Snake"
        with pytest.raises(InvalidPack, match="snake_case"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_bad_version_rejected(self) -> None:
        data = _pack_data()
        data["version"] = "1.0"
        with pytest.raises(InvalidPack, match="semver"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_empty_tree_rejected(self) -> None:
        data = _pack_data()
        data["category_tree"] = {}
        with pytest.raises(InvalidPack, match="category_tree"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_deep_tree_rejected(self) -> None:
        data = _pack_data()
        # Build a properly nested chain: a -> children.b -> children.c -> ...
        node: dict = {"children": {}}
        data["category_tree"] = {"a": node}
        current = node
        for _ in range(6):
            current["children"]["deeper"] = {"children": {}}
            current = current["children"]["deeper"]
        with pytest.raises(InvalidPack, match="4 levels"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_invalid_schema_rejected(self) -> None:
        data = _pack_data()
        data["output_schema"] = {"type": "string"}
        with pytest.raises(InvalidPack, match="output_schema"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_missing_instructions_rejected(self) -> None:
        data = _pack_data()
        del data["coding_instructions"]
        with pytest.raises(InvalidPack, match="coding_instructions"):
            parse_pack(data, origin="test", path=Path("pack.yaml"))

    def test_missing_dir_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(InvalidPack, match=r"pack\.yaml"):
            load_pack_from_dir(tmp_path, origin="test")

    def test_invalid_yaml_rejected(self, tmp_path: Path) -> None:
        d = tmp_path / "broken"
        d.mkdir()
        (d / "pack.yaml").write_text("name: [unclosed", encoding="utf-8")
        with pytest.raises(InvalidPack, match="YAML"):
            load_pack_from_dir(d, origin="test")


class TestProjectPacks:
    def test_project_pack_discovered_and_overrides(self, tmp_path: Path) -> None:
        project_packs = tmp_path / "packs"
        override = project_packs / "open_coding"
        override.mkdir(parents=True)
        data = copy.deepcopy(_pack_data())
        data["version"] = "1.1.0"
        data["coding_instructions"] = "Patched instructions."
        (override / "pack.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        pack = load_pack("open_coding", project_packs_dir=project_packs)
        assert pack.version == "1.1.0"
        assert pack.origin == "project"

        dirs = discover_packs(project_packs)
        assert "open_coding" in dirs


class TestPackHelpers:
    def test_category_paths(self) -> None:
        pack = load_pack("open_coding")
        paths = pack.category_paths()
        assert "descriptive.action" in paths
        assert "process.strategy" in paths

    def test_category_descriptions(self) -> None:
        pack = load_pack("open_coding")
        descs = pack.category_descriptions()
        assert "in-vivo" in descs["descriptive.action"] or "doing" in descs["descriptive.action"]


class TestPackEngine:
    def test_system_prompt_contains_pack_elements(self) -> None:
        pack = load_pack("open_coding")
        engine = PackEngine(pack)
        prompt = engine.render_system_prompt()
        assert "Saldana" in prompt
        assert "descriptive.action" in prompt
        assert "Decision questions" in prompt
        assert "Multiple codes per segment are allowed" in prompt

    def test_system_prompt_includes_codebook(self, session, project_row) -> None:
        from qualagent.domain.models import Code, Codebook

        book = Codebook(project_id=project_row.id, version=1)
        session.add(book)
        session.commit()
        code = Code(
            codebook_id=book.id,
            name="classroom_rapport",
            definition="Positive teacher-student relationship moments",
            inclusion_criteria="mentions warmth or connection",
        )
        session.add(code)
        session.commit()
        engine = PackEngine(load_pack("open_coding"))
        prompt = engine.render_system_prompt([code])
        assert "classroom_rapport" in prompt
        assert "include: mentions warmth" in prompt

    def test_user_prompt_lists_segments_and_memories(self) -> None:
        engine = PackEngine(load_pack("open_coding"))
        prompt = engine.render_user_prompt(
            [("seg-1", "I review vocabulary nightly.", "Teacher")],
            memories=["User prefers code 'routine' over 'habit'"],
        )
        assert "ID seg-1:" in prompt
        assert "[Teacher]" in prompt
        assert "routine" in prompt
        assert "new_code_proposals" in prompt

    def test_few_shots_rendered(self) -> None:
        engine = PackEngine(load_pack("open_coding"))
        rendered = engine.render_few_shots()
        assert "Input:" in rendered and "review_routine" in rendered

    def test_output_schema_passthrough(self) -> None:
        pack = load_pack("open_coding")
        assert PackEngine(pack).output_schema == pack.output_schema
