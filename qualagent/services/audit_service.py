"""Append-only audit trail.

Create + read only (TAD D4). There are deliberately no update or delete
methods; SQLite triggers block UPDATE/DELETE as defense in depth.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func
from sqlmodel import Session, col, select

from qualagent.domain.models import AuditEvent

Actor = Literal["ai", "human", "system"]


class AuditService:
    """Emit and query audit events for one project."""

    def __init__(self, session: Session, project_id: str) -> None:
        self._session = session
        self._project_id = project_id

    def emit(
        self,
        actor: Actor,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append one audit event and commit immediately.

        Args:
            actor: Who caused the event ("ai" | "human" | "system").
            event_type: Taxonomy value from 04_DATA_MODEL section 1.
            payload: JSON-serializable details. Must never contain secrets or
                raw prompts when the provider is remote (hash instead).

        Returns:
            The persisted AuditEvent.
        """
        event = AuditEvent(
            project_id=self._project_id,
            actor=actor,
            event_type=event_type,
            payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        )
        self._session.add(event)
        self._session.commit()
        self._session.refresh(event)
        return event

    def list_events(
        self,
        *,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditEvent], int]:
        """Query the audit trail with optional filters.

        Args:
            event_type: Filter by exact event type.
            since: Only events at or after this UTC datetime.
            limit: Page size.
            offset: Page offset.

        Returns:
            (events, total matching count)
        """
        statement = select(AuditEvent).where(col(AuditEvent.project_id) == self._project_id)
        count_stmt = (
            select(func.count())
            .select_from(AuditEvent)
            .where(col(AuditEvent.project_id) == self._project_id)
        )
        if event_type is not None:
            statement = statement.where(col(AuditEvent.event_type) == event_type)
            count_stmt = count_stmt.where(col(AuditEvent.event_type) == event_type)
        if since is not None:
            if since.tzinfo is not None:
                since = since.astimezone(UTC).replace(tzinfo=None)
            statement = statement.where(col(AuditEvent.ts) >= since)
            count_stmt = count_stmt.where(col(AuditEvent.ts) >= since)
        statement = statement.order_by(col(AuditEvent.ts)).limit(limit).offset(offset)
        events = list(self._session.exec(statement))
        total = self._session.exec(count_stmt).one()
        return events, int(total)

    @staticmethod
    def payload_of(event: AuditEvent) -> dict[str, Any]:
        """Deserialize an event's payload JSON."""
        return dict(json.loads(event.payload_json))
