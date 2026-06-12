"""Unit of Work — an explicit, service-layer-friendly name over the database
manager's ambient transaction. Usage:

    with UnitOfWork(self._db):
        self._users.add(user)
        self._logs.add(audit_entry)      # same transaction, atomic
"""
from __future__ import annotations

from pywebfw.infrastructure.database.manager import BaseDatabaseManager


class UnitOfWork:
    def __init__(self, db: BaseDatabaseManager) -> None:
        self._db = db
        self._cm = None

    def __enter__(self) -> "UnitOfWork":
        self._cm = self._db.transaction()
        self._cm.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        assert self._cm is not None
        return self._cm.__exit__(exc_type, exc, tb)
