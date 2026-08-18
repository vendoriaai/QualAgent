"""Machine-level project registry (ADR-004).

Maps project id -> study path so CLI/REST/MCP can list projects whose data
lives in independent per-study SQLite databases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from qualagent.domain.errors import ProjectNotFound


@dataclass(frozen=True)
class RegistryEntry:
    """One registered project."""

    project_id: str
    name: str
    path: str  # absolute study directory path (contains .qualagent/)
    created_at: str  # ISO-8601 UTC


class ProjectRegistry:
    """JSON-file backed registry of projects on this machine."""

    def __init__(self, registry_path: Path) -> None:
        self.path = registry_path
        self._entries: dict[str, RegistryEntry] = self._load()

    def _load(self) -> dict[str, RegistryEntry]:
        if not self.path.is_file():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            pid: RegistryEntry(
                project_id=pid,
                name=item["name"],
                path=item["path"],
                created_at=item["created_at"],
            )
            for pid, item in data.items()
        }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            pid: {"name": e.name, "path": e.path, "created_at": e.created_at}
            for pid, e in self._entries.items()
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def register(self, project_id: str, name: str, path: Path) -> RegistryEntry:
        """Add (or refresh) a project entry."""
        entry = RegistryEntry(
            project_id=project_id,
            name=name,
            path=str(path.resolve()),
            created_at=datetime.now(UTC).isoformat(),
        )
        self._entries[project_id] = entry
        self._save()
        return entry

    def unregister(self, project_id: str) -> None:
        """Remove a project entry; raises ProjectNotFound if absent."""
        if project_id not in self._entries:
            raise ProjectNotFound(f"Project {project_id} is not registered")
        del self._entries[project_id]
        self._save()

    def get(self, project_id: str) -> RegistryEntry:
        """Return the entry for a project id."""
        try:
            return self._entries[project_id]
        except KeyError:
            raise ProjectNotFound(f"Project {project_id} is not registered") from None

    def find_by_name(self, name: str) -> RegistryEntry | None:
        """Return the entry whose project name matches, if any."""
        for entry in self._entries.values():
            if entry.name == name:
                return entry
        return None

    def list_all(self) -> list[RegistryEntry]:
        """Return all registered entries ordered by creation time."""
        return sorted(self._entries.values(), key=lambda e: e.created_at)
