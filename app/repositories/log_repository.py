from __future__ import annotations

from typing import Any

from app.domain.models import AuditLog
from app.repositories.base import BaseRepository


class LogRepository(BaseRepository[AuditLog]):
    @property
    def table_name(self) -> str:
        return "audit_logs"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "actor", "action", "level", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> AuditLog:
        return AuditLog(
            id=row["id"],
            actor=row["actor"],
            action=row["action"],
            target=row["target"],
            detail=row["detail"],
            level=row["level"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: AuditLog) -> dict[str, Any]:
        return {
            "actor": entity.actor,
            "action": entity.action,
            "target": entity.target,
            "detail": entity.detail,
            "level": entity.level,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def delete_older_than(self, iso_timestamp: str) -> int:
        """Used by the cleanup scheduler job. Returns rows removed."""
        return self._db.execute("DELETE FROM audit_logs WHERE created_at < ?", (iso_timestamp,))

    def count_by_level(self) -> dict[str, int]:
        rows = self._db.fetch_all(
            "SELECT level, COUNT(*) AS n FROM audit_logs GROUP BY level")
        return {row["level"]: row["n"] for row in rows}
