"""Segmentation: split document text into codable units with exact offsets.

Strategies (segmenter_version in parentheses):
- ``sentence``  (sentence@1)  abbreviation-safe sentence splitter
- ``paragraph`` (paragraph@1) blocks separated by one or more blank lines
- ``utterance`` (utterance@1) each non-empty line is one segment
- ``turns``     (turns@1)     interview turns starting with ``Speaker:``

All strategies return offsets into ``Document.raw_text`` so that
``raw_text[start_offset:end_offset]`` round-trips exactly (TAD D3). Re-running
segmentation replaces a document's segments (the old rows are deleted, never
mutated); assignments referencing deleted segments are also removed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlmodel import Session, col, select

from qualagent.domain.errors import DocumentNotFound, SegmentNotFound, ValidationError
from qualagent.domain.models import Assignment, Document, Segment

Strategy = Literal["sentence", "paragraph", "utterance", "turns"]
STRATEGIES: tuple[Strategy, ...] = ("sentence", "paragraph", "utterance", "turns")

#: Lower-case abbreviations whose trailing period is NOT a sentence boundary.
#: Comparison is dot-insensitive (see _ABBREV_NORMALIZED); single letters cover
#: the first period of "e.g." / "i.e." / "U.S.".
_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "e.g",
        "i.e",
        "u.s",
        "u.k",
        "fig",
        "vol",
        "pp",
        "ed",
        "eds",
        "rev",
        "hon",
        "capt",
        "lt",
        "col",
        "gen",
        "no",
        "approx",
        "min",
        "max",
    }
)
_ABBREV_NORMALIZED = {a.replace(".", "") for a in _ABBREVIATIONS} | {"e", "i", "u"}

_SENT_END = re.compile(r"[.!?]+")
_TURN_PREFIX = re.compile(r"^([A-Za-z][\w.\- ]{0,40}):\s*(.*)$", re.MULTILINE)


@dataclass(frozen=True)
class SegmentDraft:
    """A planned segment before persistence."""

    start_offset: int
    end_offset: int
    speaker: str | None
    segmenter_version: str


def _trim(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Return trimmed (start, end) for a span, or None if it is whitespace-only."""
    s = start
    while s < end and text[s].isspace():
        s += 1
    e = end
    while e > s and text[e - 1].isspace():
        e -= 1
    return (s, e) if s < e else None


def _version(strategy: Strategy) -> str:
    return f"{strategy}@1"


def segment_text(raw_text: str, strategy: Strategy) -> list[SegmentDraft]:
    """Split ``raw_text`` into segment drafts using ``strategy``.

    Every draft's offsets satisfy ``raw_text[s:e]`` is non-empty and trimmed.
    """
    if strategy not in STRATEGIES:
        raise ValidationError(f"Unknown segmentation strategy: {strategy!r}")
    if strategy == "sentence":
        return _split_sentences(raw_text)
    if strategy == "paragraph":
        return _split_paragraphs(raw_text)
    if strategy == "utterance":
        return _split_utterances(raw_text)
    return _split_turns(raw_text)


def _split_sentences(text: str) -> list[SegmentDraft]:
    version = _version("sentence")
    drafts: list[SegmentDraft] = []
    seg_start = 0
    cursor = 0
    n = len(text)
    while cursor < n:
        match = _SENT_END.search(text, cursor)
        if match is None:
            span = _trim(text, seg_start, n)
            if span:
                drafts.append(SegmentDraft(span[0], span[1], None, version))
            break
        end = match.end()
        # Abbreviation guard: word immediately before the punctuation. When the
        # boundary is an abbreviation period, keep scanning without closing the
        # current segment (advance only the cursor, not seg_start).
        preceding = text[seg_start:end]
        word_match = re.search(r"([A-Za-z][A-Za-z.]*)\W*$", preceding[:-1])
        if word_match and word_match.group(1).lower().replace(".", "") in _ABBREV_NORMALIZED:
            cursor = end
            continue
        span = _trim(text, seg_start, end)
        if span:
            drafts.append(SegmentDraft(span[0], span[1], None, version))
        seg_start = end
        while seg_start < n and text[seg_start].isspace():
            seg_start += 1
        cursor = seg_start
    return drafts


def _split_by_delim(text: str, delim: re.Pattern[str], version: str) -> list[SegmentDraft]:
    """Split on a delimiter regex, emitting trimmed non-empty spans."""
    drafts: list[SegmentDraft] = []
    pos = 0
    for match in delim.finditer(text):
        span = _trim(text, pos, match.start())
        if span:
            drafts.append(SegmentDraft(span[0], span[1], None, version))
        pos = match.end()
    span = _trim(text, pos, len(text))
    if span:
        drafts.append(SegmentDraft(span[0], span[1], None, version))
    return drafts


def _split_paragraphs(text: str) -> list[SegmentDraft]:
    return _split_by_delim(text, re.compile(r"\n{2,}"), _version("paragraph"))


def _split_utterances(text: str) -> list[SegmentDraft]:
    return _split_by_delim(text, re.compile(r"\n"), _version("utterance"))


def _split_turns(text: str) -> list[SegmentDraft]:
    """Interview turns: each ``Speaker: text`` line starts a new segment.

    Continuation lines (no speaker prefix) are appended to the current turn.
    Lines before the first speaker prefix form their own speaker-less segment.
    """
    version = _version("turns")
    drafts: list[SegmentDraft] = []
    matches = list(_TURN_PREFIX.finditer(text))
    if not matches:
        return _split_utterances(text)
    # Text before the first turn prefix becomes a leading segment.
    if matches[0].start() > 0:
        span = _trim(text, 0, matches[0].start())
        if span:
            drafts.append(SegmentDraft(span[0], span[1], None, version))
    for i, match in enumerate(matches):
        speaker = match.group(1).strip()
        seg_start = match.start()
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        span = _trim(text, seg_start, seg_end)
        if span:
            drafts.append(SegmentDraft(span[0], span[1], speaker, version))
    return drafts


class SegmentationService:
    """Persist segments for a document."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def segment(
        self,
        document: Document,
        strategy: Strategy,
        *,
        replace: bool = False,
    ) -> list[Segment]:
        """Create segments for ``document``; optionally replacing existing ones."""
        if replace:
            self._delete_segments(document.id)
        existing = self._session.exec(
            select(Segment).where(col(Segment.document_id) == document.id)
        ).all()
        if existing and not replace:
            raise ValidationError(f"Document {document.id} already has segments; use replace=True")
        drafts = segment_text(document.raw_text, strategy)
        segments: list[Segment] = []
        for index, draft in enumerate(drafts):
            seg = Segment(
                document_id=document.id,
                index=index,
                start_offset=draft.start_offset,
                end_offset=draft.end_offset,
                speaker=draft.speaker,
                segmenter_version=draft.segmenter_version,
            )
            self._session.add(seg)
            segments.append(seg)
        self._session.commit()
        for seg in segments:
            self._session.refresh(seg)
        return segments

    def _delete_segments(self, document_id: str) -> None:
        seg_ids = [
            s.id
            for s in self._session.exec(
                select(Segment).where(col(Segment.document_id) == document_id)
            )
        ]
        if not seg_ids:
            return
        # Remove assignments pointing at the old segments, then the segments.
        for seg_id in seg_ids:
            for asg in self._session.exec(
                select(Assignment).where(col(Assignment.segment_id) == seg_id)
            ):
                self._session.delete(asg)
            seg = self._session.get(Segment, seg_id)
            if seg is not None:
                self._session.delete(seg)
        self._session.commit()

    def list_segments(
        self,
        document_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Segment], int]:
        """List segments for a document, ordered by index."""
        stmt = select(Segment).where(col(Segment.document_id) == document_id)
        total = self._session.exec(
            select(Segment).where(col(Segment.document_id) == document_id)
        ).all()
        stmt = stmt.order_by(col(Segment.index)).limit(limit).offset(offset)
        return list(self._session.exec(stmt)), len(total)

    def get_segment(self, segment_id: str) -> Segment:
        seg = self._session.get(Segment, segment_id)
        if seg is None:
            raise SegmentNotFound(f"Segment {segment_id} not found")
        return seg

    def get_document(self, document_id: str, project_id: str) -> Document:
        doc = self._session.get(Document, document_id)
        if doc is None or doc.project_id != project_id:
            raise DocumentNotFound(f"Document {document_id} not found")
        return doc
