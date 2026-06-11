from __future__ import annotations

from typing import Any

from pywebfw.domain.models import MenuArea, MenuItem
from pywebfw.repositories.base import BaseRepository


class MenuRepository(BaseRepository[MenuItem]):
    @property
    def table_name(self) -> str:
        return "menus"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "title", "position", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> MenuItem:
        return MenuItem(
            id=row["id"],
            title=row["title"],
            url=row["url"],
            area=MenuArea(row["area"]),
            parent_id=row["parent_id"],
            position=row["position"],
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: MenuItem) -> dict[str, Any]:
        return {
            "title": entity.title,
            "url": entity.url,
            "area": entity.area.value,
            "parent_id": entity.parent_id,
            "position": entity.position,
            "is_active": int(entity.is_active),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def list_active_by_area(self, area: MenuArea) -> list[MenuItem]:
        rows = self._db.fetch_all(
            "SELECT * FROM menus WHERE area = ? AND is_active = 1 ORDER BY position, id",
            (area.value,),
        )
        return [self._map_row(r) for r in rows]
