"""CLI end-to-end tests: init -> import -> segment (roadmap Phase 2 acceptance)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qualagent.cli.main import app

runner = CliRunner()


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated QUALAGENT_HOME + cwd; returns the working directory."""
    monkeypatch.setenv("QUALAGENT_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def _study_dir(cwd: Path, name: str = "demo") -> Path:
    return cwd / name / ".qualagent"


class TestInit:
    def test_init_creates_study(self, workdir: Path) -> None:
        result = runner.invoke(app, ["init", "demo", "--pack", "thematic_analysis@1.0.0"])
        assert result.exit_code == 0, result.output
        pdir = _study_dir(workdir)
        assert (pdir / "qualagent.db").is_file()
        assert "demo" in result.output

    def test_init_existing_name_exits_2(self, workdir: Path) -> None:
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        result = runner.invoke(app, ["init", "demo"])
        assert result.exit_code == 2


class TestImport:
    def test_import_all_formats_prints_ids_and_counts(self, workdir: Path, tmp_path: Path) -> None:
        from tests.fixtures import make_fixtures

        fixtures = make_fixtures(tmp_path / "fx")
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        pdir = _study_dir(workdir)
        args = ["--project-dir", str(pdir), "import"]
        args += [str(p) for p in fixtures.values()]
        result = runner.invoke(app, args)
        assert result.exit_code == 0, result.output
        assert result.output.count("segments") >= 5

    def test_import_json_output(self, workdir: Path, tmp_path: Path) -> None:
        src = tmp_path / "one.txt"
        src.write_text("Hello there. Second sentence.", encoding="utf-8")
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        pdir = _study_dir(workdir)
        result = runner.invoke(app, ["--project-dir", str(pdir), "--json", "import", str(src)])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["segment_count"] == 2

    def test_import_unsupported_exits_3(self, workdir: Path, tmp_path: Path) -> None:
        bad = tmp_path / "file.xyz"
        bad.write_bytes(b"\x00\x01")
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        pdir = _study_dir(workdir)
        result = runner.invoke(app, ["--project-dir", str(pdir), "import", str(bad)])
        assert result.exit_code == 3

    def test_import_without_project_fails(self, workdir: Path, tmp_path: Path) -> None:
        src = tmp_path / "one.txt"
        src.write_text("Hello.", encoding="utf-8")
        result = runner.invoke(app, ["--project-dir", str(workdir / "none"), "import", str(src)])
        assert result.exit_code == 1


class TestSegment:
    def test_resegment_turns(self, workdir: Path, tmp_path: Path) -> None:
        src = tmp_path / "turns.txt"
        src.write_text(
            "Interviewer: How long have you taught?\nTeacher: About ten years now.",
            encoding="utf-8",
        )
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        pdir = _study_dir(workdir)
        imported = runner.invoke(app, ["--project-dir", str(pdir), "--json", "import", str(src)])
        doc_id = json.loads(imported.output)[0]["document_id"]
        result = runner.invoke(
            app,
            ["--project-dir", str(pdir), "--json", "segment", doc_id, "--strategy", "turns"],
        )
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["segment_count"] == 2

    def test_segment_unknown_strategy_exits_3(self, workdir: Path) -> None:
        assert runner.invoke(app, ["init", "demo"]).exit_code == 0
        pdir = _study_dir(workdir)
        result = runner.invoke(
            app, ["--project-dir", str(pdir), "segment", "any", "--strategy", "word"]
        )
        assert result.exit_code == 3
