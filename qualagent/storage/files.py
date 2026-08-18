"""Raw document store: content-addressed by SHA-256.

Originals live at ``{project_dir}/files/{sha256}``. Storing by hash
de-duplicates re-imports and gives provenance for every document.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    """Return the hex SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


class FileStore:
    """Content-addressed store for original documents."""

    def __init__(self, project_dir: Path) -> None:
        self.root = project_dir / "files"
        self.root.mkdir(parents=True, exist_ok=True)

    def store(self, data: bytes) -> str:
        """Store ``data`` under its hash; returns the sha256 hex digest."""
        digest = sha256_bytes(data)
        target = self.root / digest
        if not target.exists():
            target.write_bytes(data)
        return digest

    def path_for(self, digest: str) -> Path:
        """Return the stored file path for a hash."""
        return self.root / digest

    def read(self, digest: str) -> bytes:
        """Return the stored bytes for a hash."""
        return self.path_for(digest).read_bytes()

    def exists(self, digest: str) -> bool:
        """Return True if a hash is stored."""
        return self.path_for(digest).exists()
