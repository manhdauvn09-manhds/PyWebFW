from __future__ import annotations

from typing import Any

from pywebfw.domain.models import Redirect
from pywebfw.repositories.base import BaseRepository


class RedirectRepository(BaseRepository[Redirect]):
    @property
    def table_name(self) -> str:
        return "redirects"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "from_path", "hits", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> Redirect:
        return Redirect(
            id=row["id"],
            from_path=row["from_path"],
            to_path=row["to_path"],
            status_code=row["status_code"],
            hits=row["hits"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: Redirect) -> dict[str, Any]:
        return {
            "from_path": entity.from_path,
            "to_path": entity.to_path,
            "status_code": entity.status_code,
            "hits": entity.hits,
            "is_active": int(entity.is_active),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def find_by_from_path(self, from_path: str) -> Redirect | None:
        row = self._db.fetch_one(
            "SELECT * FROM redirects WHERE from_path = ? AND is_active = 1", (from_path,))
        return self._map_row(row) if row else None

    def exists_from_path(self, from_path: str, exclude_id: int | None = None) -> bool:
        sql = "SELECT COUNT(*) AS n FROM redirects WHERE from_path = ?"
        params: list[Any] = [from_path]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        row = self._db.fetch_one(sql, params)
        return bool(row and row["n"] > 0)

    def list_active(self) -> list[Redirect]:
        rows = self._db.fetch_all("SELECT * FROM redirects WHERE is_active = 1")
        return [self._map_row(r) for r in rows]

    def increment_hits(self, redirect_id: int) -> None:
        self._db.execute(
            "UPDATE redirects SET hits = hits + 1 WHERE id = ?", (redirect_id,))
