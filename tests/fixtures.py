"""Generate the five Phase 2 fixture files (txt, docx, pdf, srt, csv) in a temp dir.

Generated at runtime so the repo ships no binary blobs and tests stay
self-contained.
"""

from __future__ import annotations

from pathlib import Path

SAMPLE_TURNS = [
    ("Interviewer", "Thanks for joining. Can you describe your teaching?"),
    ("Teacher", "Sure. I teach grammar to adult learners. The students are motivated."),
    ("Interviewer", "How do they respond to corrective feedback?"),
    ("Teacher", "They appreciate it, especially when it is immediate and clear."),
]


def make_fixtures(root: Path) -> dict[str, Path]:
    """Create the five fixture files under ``root`` and return them by extension."""
    root.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # TXT
    txt = root / "interview.txt"
    txt.write_text("\n".join(f"{sp}: {line}" for sp, line in SAMPLE_TURNS), encoding="utf-8")
    paths["txt"] = txt

    # DOCX
    import docx

    doc = docx.Document()
    for sp, line in SAMPLE_TURNS:
        doc.add_paragraph(f"{sp}: {line}")
    doc.save(root / "interview.docx")
    paths["docx"] = root / "interview.docx"

    # PDF
    _write_pdf(root / "interview.pdf", SAMPLE_TURNS)
    paths["pdf"] = root / "interview.pdf"

    # SRT (no speaker; plain content lines)
    srt = root / "interview.srt"
    srt.write_text(_srt_text(SAMPLE_TURNS), encoding="utf-8")
    paths["srt"] = srt

    # CSV with speaker column
    csv = root / "interview.csv"
    csv.write_text(_csv_text(SAMPLE_TURNS), encoding="utf-8")
    paths["csv"] = csv

    return paths


def _write_pdf(path: Path, turns: list[tuple[str, str]]) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    width = 72
    height = 760
    for sp, line in turns:
        c.drawString(width, height, f"{sp}: {line}")
        height -= 20
    c.showPage()
    c.save()


def _srt_text(turns: list[tuple[str, str]]) -> str:
    blocks = []
    for i, (_sp, line) in enumerate(turns, start=1):
        blocks.append(f"{i}\n00:00:{i:02d},000 --> 00:00:{i + 1:02d},000\n{line}\n")
    return "\n".join(blocks)


def _csv_text(turns: list[tuple[str, str]]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["speaker", "text"])
    for sp, line in turns:
        writer.writerow([sp, line])
    return buf.getvalue()
