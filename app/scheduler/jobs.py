"""Built-in scheduled jobs. Each one is a small `BaseSchedulerJob` subclass —
add new automation by writing another subclass and registering it in bootstrap.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.exceptions import SchedulerError
from app.infrastructure.cache.manager import BaseCacheManager, InMemoryCacheManager
from app.infrastructure.database.manager import BaseDatabaseManager
from app.repositories.log_repository import LogRepository
from app.scheduler.base import (
    BaseSchedulerJob,
    DailyTimeSchedule,
    IntervalSchedule,
    RetryPolicy,
)
from app.services.menu_service import MenuService
from app.services.system_service import ServerHealthChecker


class ServerHealthCheckJob(BaseSchedulerJob):
    name = "server-health-check"
    schedule = IntervalSchedule(60)
    retry_policy = RetryPolicy(max_attempts=2, delay_seconds=1)

    def __init__(self, checker: ServerHealthChecker) -> None:
        super().__init__()
        self._checker = checker

    def run(self) -> str:
        report = self._checker.check()
        if not report["healthy"]:
            raise SchedulerError(f"Server unhealthy: {report}")
        return f"uptime={report['uptime_seconds']}s disk_free={report['disk_free_mb']}MB"


class DatabaseHealthCheckJob(BaseSchedulerJob):
    name = "database-health-check"
    schedule = IntervalSchedule(60)
    retry_policy = RetryPolicy(max_attempts=3, delay_seconds=2)

    def __init__(self, db: BaseDatabaseManager) -> None:
        super().__init__()
        self._db = db

    def run(self) -> str:
        report = self._db.health_check()
        if not report["healthy"]:
            raise SchedulerError(f"Database unhealthy: {report.get('error')}")
        return f"latency={report['latency_ms']}ms pool={report['pool']}"


class LogCleanupJob(BaseSchedulerJob):
    """Auto-clean junk: prunes audit logs older than the retention window."""

    name = "log-cleanup"
    schedule = IntervalSchedule(3600)

    def __init__(self, logs: LogRepository, retention_days: int = 30) -> None:
        super().__init__()
        self._logs = logs
        self._retention = timedelta(days=retention_days)

    def run(self) -> str:
        cutoff = (datetime.now(timezone.utc) - self._retention).isoformat(timespec="seconds")
        deleted = self._logs.delete_older_than(cutoff)
        return f"deleted {deleted} old audit log(s)"


class CacheWarmupJob(BaseSchedulerJob):
    """Cache refresh/warming: drops expired entries (in-memory backend only —
    Redis expires keys itself), preloads hot data."""

    name = "cache-warmup"
    schedule = IntervalSchedule(240)

    def __init__(self, menus: MenuService, cache: BaseCacheManager) -> None:
        super().__init__()
        self._menus = menus
        self._cache = cache

    def run(self) -> str:
        purged = (self._cache.purge_expired()
                  if isinstance(self._cache, InMemoryCacheManager) else 0)
        warmed = self._menus.warm_cache()
        return f"purged {purged} expired entries, warmed {warmed} menu item(s)"


class DatabaseOptimizeJob(BaseSchedulerJob):
    """Index statistics / engine maintenance during off-peak hours."""

    name = "database-optimize"
    schedule = DailyTimeSchedule(hour=3, minute=30)

    def __init__(self, db: BaseDatabaseManager) -> None:
        super().__init__()
        self._db = db

    def run(self) -> str:
        report = self._db.optimize()
        return f"optimize finished in {report['duration_ms']}ms"


class DatabaseBackupJob(BaseSchedulerJob):
    """Nightly on-line backup of the SQLite database (VACUUM INTO produces a
    consistent snapshot without locking writers), keeping the newest N copies.
    Non-SQLite engines should rely on pg_dump / managed backups instead."""

    name = "database-backup"
    schedule = DailyTimeSchedule(hour=2, minute=30)
    retry_policy = RetryPolicy(max_attempts=2, delay_seconds=5)

    def __init__(self, db: BaseDatabaseManager, db_path: str, keep: int = 7) -> None:
        super().__init__()
        self._db = db
        self._db_path = db_path
        self._keep = keep

    def run(self) -> str:
        if self._db.dialect != "sqlite":
            return "skipped (non-sqlite engine: use pg_dump / managed backups)"
        if self._db_path == ":memory:":
            return "skipped (in-memory database)"
        backup_dir = Path(self._db_path).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = backup_dir / f"backup-{stamp}.db"
        escaped = str(target).replace("'", "''")
        self._db.execute(f"VACUUM INTO '{escaped}'")
        removed = self._rotate(backup_dir)
        return f"backup {target.name} created, {removed} old backup(s) removed"

    def _rotate(self, backup_dir: Path) -> int:
        backups = sorted(backup_dir.glob("backup-*.db"))
        removed = 0
        for old in backups[:-self._keep]:
            old.unlink(missing_ok=True)
            removed += 1
        return removed


class IdleConnectionCloserJob(BaseSchedulerJob):
    """Closes DB connections idle longer than the configured timeout."""

    name = "idle-connection-closer"
    schedule = IntervalSchedule(60)

    def __init__(self, db: BaseDatabaseManager, idle_timeout_seconds: int) -> None:
        super().__init__()
        self._db = db
        self._timeout = idle_timeout_seconds

    def run(self) -> str:
        closed = self._db.close_idle_connections(self._timeout)
        return f"closed {closed} idle connection(s)"
