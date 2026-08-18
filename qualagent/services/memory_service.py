"""Vector memory over user corrections and code definitions (FR-9, TAD D7).

Uses ChromaDB's default ONNX all-MiniLM-L6-v2 embedding function — fully
local, no PyTorch (ADR-001). One persistent collection per project under
``{project_dir}/chroma``. ``MemoryItem`` rows mirror what is embedded so the
audit trail and exports can reference memory without the vector store.
"""

from __future__ import annotations

from pathlib import Path

from sqlmodel import Session, col, select

from qualagent.config import QualAgentConfig
from qualagent.domain.models import MemoryItem


class MemoryService:
    """Store and retrieve project memory (corrections, definitions, notes)."""

    def __init__(
        self,
        session: Session,
        project_id: str,
        project_dir: Path,
        config: QualAgentConfig | None = None,
        client: object | None = None,  # test injection (chroma ClientAPI)
    ) -> None:
        self._session = session
        self._project_id = project_id
        self._config = config
        self._client = client if client is not None else self._make_client(project_dir)
        self._collection = self._client.get_or_create_collection(  # type: ignore[attr-defined]
            name=f"memory_{project_id.replace('-', '_')}",
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _make_client(project_dir: Path) -> object:
        import chromadb

        return chromadb.PersistentClient(path=str(project_dir / "chroma"))

    def add_correction(
        self,
        text: str,
        *,
        old_code: str | None = None,
        new_code: str | None = None,
        note: str | None = None,
    ) -> MemoryItem:
        """Record a human correction as memory + embedding.

        Args:
            text: Correction description (usually auto-composed from args).
            old_code / new_code / note: Structured fields folded into ``text``
                when ``text`` describes a code reassignment.
        """
        if not text.strip():
            composed = []
            if old_code or new_code:
                composed.append(f"User corrected code assignment: {old_code} -> {new_code}.")
            if note:
                composed.append(f"Note: {note}")
            text = " ".join(composed)
        item = MemoryItem(project_id=self._project_id, kind="correction", text=text)
        self._session.add(item)
        self._session.commit()
        self._session.refresh(item)
        self._collection.add(
            ids=[item.id],
            documents=[text],
            metadatas=[{"kind": "correction"}],
        )
        item.chroma_id = item.id
        self._session.add(item)
        self._session.commit()
        return item

    def add_definition(self, code_name: str, definition: str) -> MemoryItem:
        """Record a code definition for retrieval during coding."""
        text = f"Code {code_name}: {definition}"
        item = MemoryItem(project_id=self._project_id, kind="definition", text=text, chroma_id="")
        self._session.add(item)
        self._session.commit()
        self._session.refresh(item)
        self._collection.add(ids=[item.id], documents=[text], metadatas=[{"kind": "definition"}])
        item.chroma_id = item.id
        self._session.add(item)
        self._session.commit()
        return item

    def retrieve(self, query: str, k: int | None = None) -> list[str]:
        """Return the top-k most relevant memory texts for a query."""
        top_k = k if k is not None else (self._config.memory.top_k if self._config else 8)
        top_k = max(1, int(top_k))
        if self._collection.count() == 0:
            return []
        result = self._collection.query(query_texts=[query], n_results=top_k)
        documents = result.get("documents") or []
        if not documents:
            return []
        return list(documents[0])

    def list_items(self, kind: str | None = None) -> list[MemoryItem]:
        """List memory items, newest first."""
        stmt = select(MemoryItem).where(col(MemoryItem.project_id) == self._project_id)
        if kind is not None:
            stmt = stmt.where(col(MemoryItem.kind) == kind)
        items = list(self._session.exec(stmt.order_by(col(MemoryItem.created_at))))
        return list(reversed(items))
