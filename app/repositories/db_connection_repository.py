from __future__ import annotations

from typing import Any

from app.domain.models import DbConnectionProfile
from app.repositories.base import BaseRepository


class DbConnectionRepository(BaseRepository[DbConnectionProfile]):
    @property
    def table_name(self) -> str:
        return "db_connections"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "name", "driver", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> DbConnectionProfile:
        return DbConnectionProfile(
            id=row["id"],
            name=row["name"],
            driver=row["driver"],
            dsn=row["dsn"],
            pool_size=row["pool_size"],
            idle_timeout_seconds=row["idle_timeout_seconds"],
            is_default=bool(row["is_default"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: DbConnectionProfile) -> dict[str, Any]:
        return {
            "name": entity.name,
            "driver": entity.driver,
            "dsn": entity.dsn,
            "pool_size": entity.pool_size,
            "idle_timeout_seconds": entity.idle_timeout_seconds,
            "is_default": int(entity.is_default),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }
