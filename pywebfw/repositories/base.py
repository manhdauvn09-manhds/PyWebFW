"""Generic repository (Repository pattern + Template Method).

`BaseRepository[T]` owns the SQL skeleton for CRUD/paging; subclasses provide
the mapping between rows and entities. All SQL is parameterized, and sort
columns are validated against a per-repository whitelist (no injection via
ORDER BY).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, Sequence, TypeVar

from pywebfw.core.exceptions import NotFoundError, ValidationFailedError
from pywebfw.core.pagination import PageRequest, PageResult
from pywebfw.domain.models import BaseEntity
from pywebfw.infrastructure.database.manager import BaseDatabaseManager

T = TypeVar("T", bound=BaseEntity)


class BaseRepository(ABC, Generic[T]):
    def __init__(self, db: BaseDatabaseManager) -> None:
        self._db = db

    # --- contract subclasses implement -----------------------------------
    @property
    @abstractmethod
    def table_name(self) -> str: ...

    @property
    @abstractmethod
    def sortable_columns(self) -> frozenset[str]:
        """Whitelist for ORDER BY — defense against SQL injection via sort."""

    @abstractmethod
    def _map_row(self, row: dict[str, Any]) -> T: ...

    @abstractmethod
    def _to_params(self, entity: T) -> dict[str, Any]:
        """Column -> value mapping (excluding id)."""

    # --- generic operations ----------------------------------------------
    def get_by_id(self, entity_id: int) -> T:
        row = self._db.fetch_one(f"SELECT * FROM {self.table_name} WHERE id = ?", (entity_id,))
        if row is None:
            raise NotFoundError(f"{self.table_name[:-1]} #{entity_id} not found")
        return self._map_row(row)

    def find_by_id(self, entity_id: int) -> T | None:
        row = self._db.fetch_one(f"SELECT * FROM {self.table_name} WHERE id = ?", (entity_id,))
        return self._map_row(row) if row else None

    def list_page(
        self,
        page: PageRequest,
        where: str | None = None,
        params: Sequence[Any] = (),
    ) -> PageResult[T]:
        where_sql = f" WHERE {where}" if where else ""
        total_row = self._db.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self.table_name}{where_sql}", params)
        total = total_row["n"] if total_row else 0
        order_sql = self._order_clause(page)
        rows = self._db.fetch_all(
            f"SELECT * FROM {self.table_name}{where_sql}{order_sql} LIMIT ? OFFSET ?",
            (*params, page.size, page.offset),
        )
        return PageResult(items=[self._map_row(r) for r in rows],
                          total=total, page=page.page, size=page.size)

    def list_all(self, where: str | None = None, params: Sequence[Any] = (),
                 limit: int = 10_000) -> list[T]:
        """Bulk read for exports — bypasses page-size clamping, hard-capped."""
        where_sql = f" WHERE {where}" if where else ""
        rows = self._db.fetch_all(
            f"SELECT * FROM {self.table_name}{where_sql} ORDER BY id LIMIT ?",
            (*params, limit),
        )
        return [self._map_row(r) for r in rows]

    def count(self, where: str | None = None, params: Sequence[Any] = ()) -> int:
        where_sql = f" WHERE {where}" if where else ""
        row = self._db.fetch_one(f"SELECT COUNT(*) AS n FROM {self.table_name}{where_sql}", params)
        return row["n"] if row else 0

    def add(self, entity: T) -> T:
        data = self._to_params(entity)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        new_id = self._db.execute(
            f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        entity.id = new_id
        return entity

    def update(self, entity: T) -> T:
        if entity.id is None:
            raise ValidationFailedError("Cannot update entity without id")
        entity.touch()
        data = self._to_params(entity)
        assignments = ", ".join(f"{col} = ?" for col in data)
        affected = self._db.execute(
            f"UPDATE {self.table_name} SET {assignments} WHERE id = ?",
            (*data.values(), entity.id),
        )
        if affected == 0:
            raise NotFoundError(f"{self.table_name[:-1]} #{entity.id} not found")
        return entity

    def delete(self, entity_id: int) -> None:
        affected = self._db.execute(f"DELETE FROM {self.table_name} WHERE id = ?", (entity_id,))
        if affected == 0:
            raise NotFoundError(f"{self.table_name[:-1]} #{entity_id} not found")

    # --- helpers -----------------------------------------------------------
    def _order_clause(self, page: PageRequest) -> str:
        if not page.sort_by:
            return " ORDER BY id DESC"
        if page.sort_by not in self.sortable_columns:
            raise ValidationFailedError(f"Cannot sort by '{page.sort_by}'")
        direction = "DESC" if page.sort_desc else "ASC"
        return f" ORDER BY {page.sort_by} {direction}"
