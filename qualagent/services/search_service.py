"""Semantic search over a project's segments (MCP tool ``search_segments``).

Embeds segment text with ChromaDB's default ONNX MiniLM-L6-v2 function
(ADR-001; same embedding backend as the memory service) and ranks segments
against a query by cosine similarity. Embeddings are computed on demand per
query — segment offsets keep the source text stable (TAD D3), so segment text
equals ``Document.raw_text[start_offset:end_offset]`` exactly.

The embedder is injectable for tests: passing a deterministic in-process
embedder keeps the MCP contract suite offline and fast.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlmodel import Session, col, select

from qualagent.domain.models import Document, Segment


class Embedder(Protocol):
    """Callable mapping texts to equal-length float vectors."""

    def __call__(self, texts: list[str]) -> list[list[float]]: ...


def default_embedder() -> Any:
    """Return ChromaDB's bundled offline embedder (ONNX MiniLM, ADR-001)."""
    from chromadb.utils import embedding_functions as eff

    return eff.DefaultEmbeddingFunction()


@dataclass(frozen=True)
class SegmentMatch:
    """One ranked segment plus its source text and similarity score."""

    segment: Segment
    text: str
    score: float


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; returns 0.0 when either vector is all zeros."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


class SegmentSearchService:
    """Rank a project's segments by semantic similarity to a query."""

    def __init__(self, session: Session, *, embedder: Any | None = None) -> None:
        self._session = session
        self._embedder = embedder

    def _ensure_embedder(self) -> Any:
        if self._embedder is None:
            self._embedder = default_embedder()
        return self._embedder

    def search(
        self,
        project_id: str,
        query: str,
        k: int = 8,
    ) -> list[SegmentMatch]:
        """Return the top-k segments for ``query``, most similar first.

        Args:
            project_id: Study to search within.
            query: Natural-language search text.
            k: Maximum number of matches to return (>= 1).

        Returns:
            Up to ``k`` :class:`SegmentMatch` entries, descending by score.
            Returns an empty list when the study has no segments.
        """
        if k < 1:
            k = 1
        rows = list(
            self._session.exec(
                select(Segment, Document)
                .join(Document, col(Segment.document_id) == col(Document.id))
                .where(col(Document.project_id) == project_id)
                .order_by(
                    col(Document.imported_at),
                    col(Segment.document_id),
                    col(Segment.index),
                )
            ).all()
        )
        if not rows:
            return []
        texts = [doc.raw_text[seg.start_offset : seg.end_offset] for seg, doc in rows]
        embedder = self._ensure_embedder()
        doc_vecs = embedder(list(texts))
        query_vecs = embedder([query])
        if not query_vecs:
            return []
        query_vec = query_vecs[0]
        matches = [
            SegmentMatch(segment=seg, text=text, score=_cosine(query_vec, vec))
            for (seg, doc), text, vec in zip(rows, texts, doc_vecs, strict=True)
        ]
        matches.sort(key=lambda m: m.score, reverse=True)
        return matches[:k]
