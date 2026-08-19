"""Inter-rater reliability: Cohen's kappa, AI vs human decisions (FR-8).

For every segment that has a human decision, the AI's original (pending or
later-edited) assignment is compared against what the human settled on:
approved → AI's code, rejected → special ``__rejected__`` class, edited → the
new code. Kappa is computed overall and per code with sklearn (TAD D4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sklearn.metrics import cohen_kappa_score
from sqlmodel import Session, col, select

from qualagent.domain.models import Assignment, Code
from qualagent.domain.schemas import IRRReport

#: Pseudo-class for AI codes a human rejected (no human code assigned).
REJECTED = "__rejected__"


@dataclass
class _SegmentPair:
    segment_id: str
    ai: str
    human: str


@dataclass
class IRRCalculator:
    """Compute Cohen's kappa between AI and human raters."""

    session: Session
    pairs: list[_SegmentPair] = field(default_factory=list)

    def compute(self) -> IRRReport:
        """Return overall and per-code kappa for all decided segments."""
        self._collect_pairs()
        if not self.pairs:
            return IRRReport(overall_kappa=0.0, per_code={}, n_compared=0)
        ai_labels = [p.ai for p in self.pairs]
        human_labels = [p.human for p in self.pairs]
        overall = _safe_kappa(ai_labels, human_labels)
        per_code: dict[str, float | None] = {}
        for code_name in sorted({p.ai for p in self.pairs} | {p.human for p in self.pairs}):
            # One-vs-rest binarization per code.
            ai_bin = [1 if p.ai == code_name else 0 for p in self.pairs]
            hu_bin = [1 if p.human == code_name else 0 for p in self.pairs]
            per_code[code_name] = _safe_kappa(ai_bin, hu_bin)
        return IRRReport(
            overall_kappa=overall if overall is not None else 0.0,
            per_code={k: v if v is not None else 0.0 for k, v in per_code.items()},
            n_compared=len(self.pairs),
        )

    def _collect_pairs(self) -> None:
        """Pair each segment's AI assignment with the human's final decision.

        A decision updates the same assignment row (status leaves ``pending``),
        so the AI label is the row's original code and the human label is the
        row's post-decision code (or ``__rejected__``). Rows still pending are
        undecided and skipped.
        """
        self.pairs = []
        decided = list(
            self.session.exec(select(Assignment).where(col(Assignment.status) != "pending")).all()
        )
        ai_codes: dict[str, str] = {}
        for a in self.session.exec(select(Assignment).where(col(Assignment.source) == "ai")).all():
            ai_codes[a.segment_id] = a.code_id
        names = self._code_names()
        for row in decided:
            ai_code_id = ai_codes.get(row.segment_id)
            if ai_code_id is None:
                continue
            human_code = REJECTED if row.status == "rejected" else names.get(row.code_id, "?")
            self.pairs.append(
                _SegmentPair(
                    segment_id=row.segment_id,
                    ai=names.get(ai_code_id, "?"),
                    human=human_code,
                )
            )

    def _code_names(self) -> dict[str, str]:
        return {c.id: c.name for c in self.session.exec(select(Code)).all()}


def _safe_kappa(ai: list[str] | list[int], human: list[str] | list[int]) -> float | None:
    """Kappa that degrades to None when undefined (single-class labels)."""
    if len(set(ai)) < 2 and len(set(human)) < 2:
        return None if ai != human else 1.0
    if len(set(ai)) < 2 or len(set(human)) < 2:
        # One rater constant: kappa undefined unless both constant and equal.
        if set(ai) == set(human):
            return 1.0
        return 0.0
    value = cohen_kappa_score(ai, human)
    return float(value)
