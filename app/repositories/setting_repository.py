from __future__ import annotations

from typing import Any

from app.domain.models import SettingEntry, utc_now_iso
from app.repositories.base import BaseRepository


class SettingRepository(BaseRepository[SettingEntry]):
    @property
    def table_name(self) -> str:
        return "system_settings"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "key", "updated_at"})

    def _map_row(self, row: dict[str, Any]) -> SettingEntry:
        return SettingEntry(
            id=row["id"],
            key=row["key"],
            value=row["value"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: SettingEntry) -> dict[str, Any]:
        return {
            "key": entity.key,
            "value": entity.value,
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def all_as_dict(self) -> dict[str, str]:
        rows = self._db.fetch_all("SELECT key, value FROM system_settings")
        return {row["key"]: row["value"] for row in rows}

    def upsert(self, key: str, value: str) -> None:
        now = utc_now_iso()
        self._db.execute(
            "INSERT INTO system_settings (key, value, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, now, now),
        )
