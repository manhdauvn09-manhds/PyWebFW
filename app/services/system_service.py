"""System administration: DB connection profiles + server health snapshot."""
from __future__ import annotations

import shutil
import time
from typing import Any

from app.core.pagination import PageRequest, PageResult
from app.domain.models import DbConnectionProfile
from app.infrastructure.database.manager import BaseDatabaseManager
from app.repositories.db_connection_repository import DbConnectionRepository
from app.repositories.log_repository import LogRepository
from app.services.base import AuditMixin, BaseService


class BaseHealthChecker(BaseService):
    """Contract for pluggable health checks (server, db, external APIs...)."""

    name: str = "base"

    def check(self) -> dict[str, Any]:
        raise NotImplementedError


class ServerHealthChecker(BaseHealthChecker):
    name = "server"

    def __init__(self, started_at: float) -> None:
        super().__init__()
        self._started_at = started_at

    def check(self) -> dict[str, Any]:
        disk = shutil.disk_usage(".")
        return {
            "healthy": disk.free > 500 * 1024 * 1024,   # warn under 500MB free
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "disk_free_mb": disk.free // (1024 * 1024),
        }


class DatabaseHealthChecker(BaseHealthChecker):
    name = "database"

    def __init__(self, db: BaseDatabaseManager) -> None:
        super().__init__()
        self._db = db

    def check(self) -> dict[str, Any]:
        return self._db.health_check()


class SystemService(BaseService, AuditMixin):
    def __init__(
        self,
        profiles: DbConnectionRepository,
        logs: LogRepository,
        checkers: list[BaseHealthChecker],
    ) -> None:
        super().__init__()
        self._profiles = profiles
        self._audit_repo = logs
        self._checkers = checkers

    def health_report(self) -> dict[str, Any]:
        report = {checker.name: checker.check() for checker in self._checkers}
        report["healthy"] = all(section.get("healthy", False) for section in report.values())
        return report

    def list_profiles(self, page: PageRequest) -> PageResult[DbConnectionProfile]:
        return self._profiles.list_page(page)

    def create_profile(self, profile: DbConnectionProfile, actor: str) -> DbConnectionProfile:
        self._profiles.add(profile)
        self._audit(actor, "db_connection.created", target=profile.name)
        return profile

    def delete_profile(self, profile_id: int, actor: str) -> None:
        profile = self._profiles.get_by_id(profile_id)
        self._profiles.delete(profile_id)
        self._audit(actor, "db_connection.deleted", target=profile.name, level="warning")
