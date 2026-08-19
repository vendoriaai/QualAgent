"""Phase 5 acceptance: full CLI flow — import -> code -> review -> irr -> export."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest
import yaml
from qualagent.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

TRANSCRIPT = """Interviewer: How do you prepare for exams?
Student: I review my notes every evening with flashcards.
Interviewer: Does that work well?
Student: Mostly, but I get distracted by my phone at night.
Interviewer: Any plans to change that?
Student: I want to try studying in the library instead.
"""


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUALAGENT_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def _setup_study(workdir: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Init study + import + code with a fake cassette; return (pdir, cassette)."""
    os.chdir(workdir)
    assert runner.invoke(app, ["init", "demo"]).exit_code == 0
    src = tmp_path / "interview.txt"
    src.write_text(TRANSCRIPT, encoding="utf-8")
    pdir = workdir / "demo" / ".qualagent"
    imported = runner.invoke(
        app, ["--project-dir", str(pdir), "--json", "import", str(src), "--strategy", "turns"]
    )
    assert imported.exit_code == 0, imported.output
    return pdir, src


def _segment_ids(pdir: Path) -> list[str]:
    from qualagent.runtime import open_project
    from qualagent.services.segmentation_service import SegmentationService

    pctx = open_project(pdir)
    with pctx.session() as session:
        doc_id = _first_doc(session)
        segs, _ = SegmentationService(session).list_segments(doc_id, limit=100)
        ids = [s.id for s in segs]
    pctx.engine.dispose()
    return ids


def _first_doc(session) -> str:
    from qualagent.domain.models import Document
    from sqlmodel import select

    return session.exec(select(Document)).first().id


def _cassette(tmp_path: Path, ids: list[str], codes: list[str]) -> Path:
    results = [
        {
            "segment_id": sid,
            "assignments": [
                {"code": codes[i % len(codes)], "rationale": f"why {i}", "confidence": 0.85}
            ],
            "uncodable": False,
        }
        for i, sid in enumerate(ids)
    ]
    path = tmp_path / "cassette.yaml"
    path.write_text(
        yaml.safe_dump({"responses": [{"response": {"results": results}}]}, sort_keys=False),
        encoding="utf-8",
    )
    return path


class TestFullFlow:
    def test_import_code_review_irr_export(self, workdir: Path, tmp_path: Path) -> None:
        pdir, _src = _setup_study(workdir, tmp_path)
        ids = _segment_ids(pdir)
        assert len(ids) >= 3

        # Code run with cassette.
        cassette = _cassette(tmp_path, ids, ["study_routine", "distraction"])
        run = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "code",
                "run",
                "--llm",
                "fake",
                "--cassette",
                str(cassette),
            ],
        )
        assert run.exit_code == 0, run.output

        # Review list shows pending assignments.
        listed = runner.invoke(
            app, ["--project-dir", str(pdir), "--json", "review", "list", "--status", "pending"]
        )
        assert listed.exit_code == 0, listed.output
        items = json.loads(listed.output)["items"]
        assert len(items) >= 3
        first_id = items[0]["assignment_id"]

        # Decide three ways: approve, reject, edit.
        approve = runner.invoke(
            app, ["--project-dir", str(pdir), "review", "decide", first_id, "--approve"]
        )
        assert approve.exit_code == 0, approve.output
        second_id = items[1]["assignment_id"]
        reject = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "review",
                "decide",
                second_id,
                "--reject",
                "--note",
                "code does not apply",
            ],
        )
        assert reject.exit_code == 0, reject.output
        third_id = items[2]["assignment_id"]
        target_code = "distraction" if items[2]["code"] != "distraction" else "study_routine"
        edit = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "review",
                "decide",
                third_id,
                "--edit-code",
                target_code,
                "--note",
                "better fit",
            ],
        )
        assert edit.exit_code == 0, edit.output

        # Double decision -> CONFLICT exit 2.
        again = runner.invoke(
            app, ["--project-dir", str(pdir), "review", "decide", first_id, "--reject"]
        )
        assert again.exit_code == 2

        # IRR.
        irr = runner.invoke(app, ["--project-dir", str(pdir), "--json", "irr", "compute"])
        assert irr.exit_code == 0, irr.output
        report = json.loads(irr.output)
        assert report["n_compared"] == 3

        # Exports.
        out_dir = tmp_path / "exports"
        matrix = runner.invoke(
            app,
            ["--project-dir", str(pdir), "export", "matrix", "--out", str(out_dir / "matrix.csv")],
        )
        assert matrix.exit_code == 0, matrix.output
        with (out_dir / "matrix.csv").open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert {r["code"] for r in rows} >= {"study_routine", "distraction"}

        xlsx = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "export",
                "matrix",
                "--format",
                "xlsx",
                "--out",
                str(out_dir / "matrix.xlsx"),
            ],
        )
        assert xlsx.exit_code == 0, xlsx.output
        assert (out_dir / "matrix.xlsx").exists()

        codebook = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "export",
                "codebook",
                "--out",
                str(out_dir / "codebook.md"),
            ],
        )
        assert codebook.exit_code == 0, codebook.output
        content = (out_dir / "codebook.md").read_text(encoding="utf-8")
        assert "study_routine" in content
        # Fidelity: approved quote maps to exact source offsets.
        assert "[" in content  # offset annotations present

        audit = runner.invoke(
            app,
            ["--project-dir", str(pdir), "export", "audit", "--out", str(out_dir / "audit.jsonl")],
        )
        assert audit.exit_code == 0, audit.output
        lines = (out_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines]
        types = {e["event_type"] for e in events}
        assert {"run.completed", "assignment.approved", "assignment.edited"} <= types

        methods = runner.invoke(
            app,
            ["--project-dir", str(pdir), "export", "methods", "--out", str(out_dir / "methods.md")],
        )
        assert methods.exit_code == 0, methods.output
        text = (out_dir / "methods.md").read_text(encoding="utf-8")
        assert "kappa" in text and "audit" in text

    def test_review_unknown_assignment(self, workdir: Path, tmp_path: Path) -> None:
        pdir, _ = _setup_study(workdir, tmp_path)
        bad = runner.invoke(
            app, ["--project-dir", str(pdir), "review", "decide", "nope", "--approve"]
        )
        assert bad.exit_code == 3  # VALIDATION_ERROR

    def test_export_fidelity_release_blocking(self, workdir: Path, tmp_path: Path) -> None:
        """Every exported quote must appear at its recorded offsets (headline feature)."""
        pdir, _ = _setup_study(workdir, tmp_path)
        ids = _segment_ids(pdir)
        cassette = _cassette(tmp_path, ids, ["study_routine"])
        run = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "code",
                "run",
                "--llm",
                "fake",
                "--cassette",
                str(cassette),
            ],
        )
        assert run.exit_code == 0, run.output
        listed = runner.invoke(
            app, ["--project-dir", str(pdir), "--json", "review", "list", "--status", "pending"]
        )
        items = json.loads(listed.output)["items"]
        for it in items:
            decided = runner.invoke(
                app,
                ["--project-dir", str(pdir), "review", "decide", it["assignment_id"], "--approve"],
            )
            assert decided.exit_code == 0, decided.output
        out = tmp_path / "cb.md"
        exported = runner.invoke(
            app, ["--project-dir", str(pdir), "export", "codebook", "--out", str(out)]
        )
        assert exported.exit_code == 0, exported.output

        # Parse every quote line and verify against the source document.
        from qualagent.domain.models import Document
        from qualagent.runtime import open_project
        from sqlmodel import select

        pctx = open_project(pdir)
        with pctx.session() as session:
            doc = session.exec(select(Document)).first()
            raw = doc.raw_text
        pctx.engine.dispose()
        content = out.read_text(encoding="utf-8")
        quote_lines = [ln[2:] for ln in content.splitlines() if ln.startswith("> ")]
        assert quote_lines, "no example quotes exported"
        offset_lines = [ln for ln in content.splitlines() if "—" in ln and "[" in ln]
        assert len(quote_lines) == len(offset_lines)
        for quote, offset_ln in zip(quote_lines, offset_lines, strict=True):
            start_s, end_s = offset_ln[offset_ln.index("[") + 1 : offset_ln.index("]")].split(":")
            start, end = int(start_s), int(end_s)
            assert 0 <= start < end <= len(raw)
            assert raw[start:end].replace("\n", " ") == quote, (
                f"quote at [{start}:{end}] does not match source"
            )
