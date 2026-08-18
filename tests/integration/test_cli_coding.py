"""Phase 4 acceptance: end-to-end CLI coding run offline with --llm fake."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qualagent.cli.main import app

runner = CliRunner()

TRANSCRIPT = (
    "Interviewer: How do you prepare your lessons?\n"
    "Teacher: I review vocabulary lists every evening.\n"
    "The group work was difficult at first but improved."
)

EXPECTED_TEXTS = [
    "Interviewer: How do you prepare your lessons?",
    "Teacher: I review vocabulary lists every evening.",
    "The group work was difficult at first but improved.",
]


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("QUALAGENT_HOME", str(tmp_path / "home"))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def _seed_segments(workdir: Path, tmp_path: Path) -> list[str]:
    """Create the study, import a turns transcript, return segment ids."""
    assert runner.invoke(app, ["init", "demo", "--pack", "open_coding@1.0.0"]).exit_code == 0
    src = tmp_path / "interview.txt"
    src.write_text(TRANSCRIPT, encoding="utf-8")
    pdir = workdir / "demo" / ".qualagent"
    imported = runner.invoke(
        app, ["--project-dir", str(pdir), "--json", "import", str(src), "--strategy", "turns"]
    )
    assert imported.exit_code == 0, imported.output
    doc_id = json.loads(imported.output)[0]["document_id"]
    # We just need segment ids: read them from the DB via a tiny python run.
    from qualagent.runtime import open_project
    from qualagent.services.segmentation_service import SegmentationService

    pctx = open_project(pdir)
    with pctx.session() as session:
        segs_, _ = SegmentationService(session).list_segments(doc_id, limit=50)
        ids = [s.id for s in segs_]
    pctx.engine.dispose()
    return ids


def _cassette(tmp_path: Path, seg_ids: list[str]) -> Path:
    """Write a cassette that codes the given segment ids (or garbage if empty)."""
    path = tmp_path / "cassette.yaml"
    if seg_ids:
        results = [
            {
                "segment_id": sid,
                "assignments": [
                    {
                        "code": "review_routine",
                        "rationale": "describes a deliberate routine",
                        "confidence": 0.9,
                    }
                ],
                "uncodable": False,
            }
            for sid in seg_ids
        ]
        responses = [{"response": {"results": results, "new_code_proposals": []}}]
    else:
        responses = [{"response": {"results": [], "new_code_proposals": []}}]
    path.write_text(
        yaml.safe_dump({"model": "fake/cli", "responses": responses}, sort_keys=False),
        encoding="utf-8",
    )
    return path


class TestCodeRunCli:
    def test_full_flow_fake_llm(self, workdir: Path, tmp_path: Path) -> None:
        ids = _seed_segments(workdir, tmp_path)
        assert len(ids) == 2  # two speaker turns; continuation line joins turn 2
        pdir = workdir / "demo" / ".qualagent"
        cassette = _cassette(tmp_path, ids)
        result = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "--json",
                "code",
                "run",
                "--llm",
                "fake",
                "--cassette",
                str(cassette),
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "completed"
        assert payload["stats"]["assignments"] == 2
        assert payload["stats"]["flagged"] == 0
        run_id = payload["run_id"]

        status = runner.invoke(
            app, ["--project-dir", str(pdir), "--json", "code", "status", run_id]
        )
        assert status.exit_code == 0
        assert json.loads(status.output)["status"] == "completed"

    def test_flagged_on_double_failure(self, workdir: Path, tmp_path: Path) -> None:
        ids = _seed_segments(workdir, tmp_path)
        assert len(ids) == 2
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            yaml.safe_dump(
                {"responses": [{"response": "junk"}, {"response": "junk2"}]},
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        pdir = workdir / "demo" / ".qualagent"
        result = runner.invoke(
            app,
            [
                "--project-dir",
                str(pdir),
                "--json",
                "code",
                "run",
                "--llm",
                "fake",
                "--cassette",
                str(bad),
            ],
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "completed"
        assert payload["stats"]["flagged"] == 2

    def test_audit_trail_complete(self, workdir: Path, tmp_path: Path) -> None:
        ids = _seed_segments(workdir, tmp_path)
        pdir = workdir / "demo" / ".qualagent"
        cassette = _cassette(tmp_path, ids)
        runner.invoke(
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
        from qualagent.runtime import open_project
        from qualagent.services.audit_service import AuditService

        pctx = open_project(pdir)
        with pctx.session() as session:
            audit = AuditService(session, pctx.project.id)
            events, _ = audit.list_events(limit=200)
            types = {e.event_type for e in events}
            assert {"run.started", "llm.call", "run.batch_completed", "run.completed"} <= types
            llm_calls, _ = audit.list_events(event_type="llm.call")
            payload = AuditService.payload_of(llm_calls[0])
            assert "prompt_hash" in payload and "response_hash" in payload
        pctx.engine.dispose()


class TestCodebookCli:
    def test_show_refine_lock(self, workdir: Path, tmp_path: Path) -> None:
        ids = _seed_segments(workdir, tmp_path)
        pdir = workdir / "demo" / ".qualagent"
        # A run with a proposal to create suggestions.
        prop = tmp_path / "prop.yaml"
        results = [{"segment_id": sid, "assignments": [], "uncodable": False} for sid in ids]
        prop.write_text(
            yaml.safe_dump(
                {
                    "responses": [
                        {
                            "response": {
                                "results": results,
                                "new_code_proposals": [
                                    {
                                        "name": "evening_review",
                                        "definition": "Evening study habits",
                                        "example_segment_id": ids[1],
                                    }
                                ],
                            }
                        }
                    ]
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        run = runner.invoke(
            app,
            ["--project-dir", str(pdir), "code", "run", "--llm", "fake", "--cassette", str(prop)],
        )
        assert run.exit_code == 0, run.output

        show = runner.invoke(app, ["--project-dir", str(pdir), "codebook", "show"])
        assert "suggestion_evening_review" in show.output

        refined = runner.invoke(
            app, ["--project-dir", str(pdir), "codebook", "refine", "--from-suggestions"]
        )
        assert refined.exit_code == 0, refined.output

        show2 = runner.invoke(app, ["--project-dir", str(pdir), "codebook", "show"])
        assert "evening_review" in show2.output
        assert "suggestion_evening_review" not in show2.output

        locked = runner.invoke(app, ["--project-dir", str(pdir), "codebook", "lock"])
        assert locked.exit_code == 0
        again = runner.invoke(app, ["--project-dir", str(pdir), "codebook", "lock"])
        assert again.exit_code == 6  # already locked


class TestPacksCli:
    def test_list_and_show(self, workdir: Path) -> None:
        listing = runner.invoke(app, ["packs", "list"])
        assert listing.exit_code == 0
        assert "open_coding" in listing.output
        show = runner.invoke(app, ["packs", "show", "open_coding"])
        assert "Saldana" in show.output
        missing = runner.invoke(app, ["packs", "show", "nonexistent"])
        assert missing.exit_code == 1
