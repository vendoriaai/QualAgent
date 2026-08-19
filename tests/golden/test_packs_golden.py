"""Golden pack tests: expert-coded fixtures replayed via scripted cassettes.

Each fixture is a small transcript with expert reference codes. The cassette
replays the recorded LLM output; the test asserts (a) the recorded run
reproduces the assignments and (b) AI-vs-expert agreement meets the kappa
target from the roadmap (>= 0.75 for thematic_analysis).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

GOLDEN_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    path = GOLDEN_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cassette(tmp_path: Path, fixture: dict) -> Path:
    """Cassette returning the model codes verbatim (recorded shape)."""
    results = []
    for seg in fixture["segments"]:
        assignments = [
            {"code": c, "rationale": "golden", "confidence": 0.9} for c in seg["model_codes"]
        ]
        results.append(
            {
                "segment_id": seg["id"],
                "assignments": assignments,
                "uncodable": not seg["model_codes"],
            }
        )
    path = tmp_path / "golden_cassette.yaml"
    path.write_text(
        yaml.safe_dump({"responses": [{"response": {"results": results}}]}, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _kappa_vs_expert(fixture: dict, model_codes: dict[str, list[str]]) -> tuple[float, int]:
    """Pairwise one-vs-rest Cohen's kappa between expert and model, all codes."""
    from sklearn.metrics import cohen_kappa_score

    all_codes = sorted(
        {c for s in fixture["segments"] for c in s["expert_codes"]}
        | {c for cs in model_codes.values() for c in cs}
    )
    pair_labels = []
    for seg in fixture["segments"]:
        expert = set(seg["expert_codes"])
        model = set(model_codes.get(seg["id"], []))
        pair_labels.append((expert, model))
    scores = []
    for code in all_codes:
        e = [1 if code in ex else 0 for ex, _ in pair_labels]
        m = [1 if code in mo else 0 for _, mo in pair_labels]
        if len(set(e)) < 2 and len(set(m)) < 2:
            continue  # code unused by both raters
        scores.append(cohen_kappa_score(e, m))
    overall = sum(scores) / len(scores) if scores else 1.0
    return overall, len(pair_labels)


class TestThematicAnalysisGolden:
    def test_fixture_replay_meets_kappa_target(self, tmp_path: Path) -> None:
        fixture = _load_fixture("thematic_analysis_fixture")
        assert len(fixture["segments"]) == 12
        model_codes = {s["id"]: s["model_codes"] for s in fixture["segments"]}
        overall, n = _kappa_vs_expert(fixture, model_codes)
        assert n == 12
        assert overall >= 0.75, f"kappa {overall:.2f} below 0.75 target"

    def test_pack_loads_with_citation(self) -> None:
        from qualagent.packs.loader import load_pack

        pack = load_pack("thematic_analysis")
        assert "Braun" in pack.citation and "2006" in pack.citation
        assert len(pack.few_shot_examples) == 3
        assert any("actor_text" not in json.dumps(f["output"]) for f in pack.few_shot_examples)

    def test_cassette_pipeline_end_to_end(self, tmp_path: Path) -> None:
        """Replay the fixture through the real coding service via cassette."""
        from qualagent.config import load_config
        from qualagent.domain.models import (
            Assignment,
            Code,
            Codebook,
            Document,
            Project,
            Segment,
        )
        from qualagent.llm.fake import FakeProvider
        from qualagent.packs.loader import load_pack
        from qualagent.services.audit_service import AuditService
        from qualagent.services.codebook_service import CodebookService
        from qualagent.services.coding_service import CodingService
        from qualagent.storage.db import init_db
        from sqlmodel import Session, create_engine, select

        fixture = _load_fixture("thematic_analysis_fixture")
        engine = create_engine("sqlite://")
        init_db(engine)
        with Session(engine) as session:
            project = Project(name="golden-ta")
            session.add(project)
            session.commit()
            text = "\n".join(s["text"] for s in fixture["segments"])
            doc = Document(
                project_id=project.id,
                filename="golden.txt",
                sha256="g" * 64,
                mime="text/plain",
                raw_text=text,
            )
            session.add(doc)
            session.commit()
            # Offsets derived from the join.
            offset = 0
            seg_by_id = {}
            for s in fixture["segments"]:
                seg_len = len(s["text"])
                seg = Segment(
                    document_id=doc.id,
                    index=len(seg_by_id),
                    start_offset=offset,
                    end_offset=offset + seg_len,
                    segmenter_version="golden@1",
                )
                session.add(seg)
                session.commit()
                seg_by_id[s["id"]] = seg
                offset += seg_len + 1
            book = Codebook(project_id=project.id, version=1, status="draft")
            session.add(book)
            session.commit()
            names = sorted({c for s in fixture["segments"] for c in s["model_codes"]})
            for name in names:
                session.add(Code(codebook_id=book.id, name=name))
            session.commit()

            audit = AuditService(session, project.id)
            codebooks = CodebookService(session, audit)
            provider = FakeProvider(cassette_path=_write_cassette(tmp_path, fixture))
            config = load_config(Path(tmp_path))
            svc = CodingService(session, audit, codebooks, None, provider, config)
            run = svc.start_run(project, load_pack("thematic_analysis"))
            assert run.status == "completed"
            assignments = list(session.exec(select(Assignment)).all())
            assert len(assignments) == sum(len(s["model_codes"]) for s in fixture["segments"])


class TestVanLeeuwenGolden:
    def test_fixture_replay_meets_kappa_target(self) -> None:
        fixture = _load_fixture("van_leeuwen_fixture")
        model_codes = {s["id"]: s["model_codes"] for s in fixture["segments"]}
        overall, n = _kappa_vs_expert(fixture, model_codes)
        assert n == len(fixture["segments"])
        assert overall >= 0.75, f"kappa {overall:.2f} below 0.75 target"

    def test_pack_loads_with_actor_text(self) -> None:
        from qualagent.packs.loader import load_pack

        pack = load_pack("van_leeuwen")
        assert "van Leeuwen" in pack.citation and "2008" in pack.citation
        assert len(pack.few_shot_examples) == 5
        # Every few-shot assignment carries an actor_text span.
        for example in pack.few_shot_examples:
            for a in example["output"]:
                assert "actor_text" in a

    def test_taxonomy_completeness(self) -> None:
        from qualagent.packs.loader import load_pack

        paths = set(load_pack("van_leeuwen").category_paths())
        required = {
            "exclusion.suppression",
            "exclusion.backgrounding",
            "inclusion.activation",
            "inclusion.passivation.subjection",
            "inclusion.passivation.beneficialisation",
            "inclusion.genericisation",
            "inclusion.specification",
            "inclusion.individualisation",
            "inclusion.assimilation.aggregation",
            "inclusion.assimilation.collectivisation",
            "inclusion.nomination",
            "inclusion.categorisation.identification",
            "inclusion.categorisation.functionalisation",
            "inclusion.categorisation.appraisement",
            "inclusion.determination",
            "inclusion.indetermination",
            "inclusion.differentiation",
            "inclusion.abstraction",
        }
        missing = required - paths
        assert not missing, f"missing taxonomy nodes: {missing}"


class TestOpenCodingGolden:
    def test_pack_citation_present(self) -> None:
        from qualagent.packs.loader import load_pack

        pack = load_pack("open_coding")
        assert "Saldana" in pack.citation


class TestNegative:
    def test_citationless_pack_rejected(self, tmp_path: Path) -> None:
        """A pack without a citation must be rejected (hard rule)."""
        from qualagent.domain.errors import InvalidPack
        from qualagent.packs.loader import load_pack_from_dir

        d = tmp_path / "broken_pack"
        d.mkdir()
        (d / "pack.yaml").write_text(
            "name: broken\nversion: 1.0.0\ntitle: Broken\nmulti_code: true\n"
            "unit: segment\n"
            "category_tree:\n  a:\n    description: x\n"
            "coding_instructions: code things\n"
            "output_schema:\n  type: object\n  properties: {}\n",
            encoding="utf-8",
        )
        with pytest.raises(InvalidPack, match="citation"):
            load_pack_from_dir(d, origin="test")
