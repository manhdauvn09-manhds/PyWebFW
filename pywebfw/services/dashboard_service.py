"""Aggregated metrics for the admin dashboard."""
from __future__ import annotations

from typing import Any

from pywebfw.core.pagination import PageRequest
from pywebfw.infrastructure.cache.manager import BaseCacheManager
from pywebfw.infrastructure.database.manager import BaseDatabaseManager
from pywebfw.repositories.content_repository import ContentRepository
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.repositories.user_repository import UserRepository
from pywebfw.services.base import BaseService
from pywebfw.services.traffic_service import TrafficService


class DashboardService(BaseService):
    def __init__(
        self,
        db: BaseDatabaseManager,
        users: UserRepository,
        logs: LogRepository,
        contents: ContentRepository,
        cache: BaseCacheManager,
        traffic: TrafficService,
    ) -> None:
        super().__init__()
        self._db = db
        self._users = users
        self._logs = logs
        self._contents = contents
        self._cache = cache
        self._traffic = traffic

    def metrics(self) -> dict[str, Any]:
        recent = self._logs.list_page(PageRequest.create(page=1, size=8))
        return {
            "counts": {
                "users": self._users.count(),
                "active_users": self._users.count("is_active = 1"),
                "contents": self._contents.count(),
                "audit_logs": self._logs.count(),
            },
            "logs_by_level": self._logs.count_by_level(),
            "recent_logs": [log.to_dict() for log in recent.items],
            "database": self._db.health_check(),
            "cache": self._cache.stats(),
            "traffic": self._traffic.dashboard_stats(),
        }
