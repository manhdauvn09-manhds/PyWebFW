"""Database backups: consistent online snapshots (VACUUM INTO) with rotation.
Owned here so both the nightly job and the admin Backup Manager share one
implementation."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pywebfw.core.exceptions import ConflictError, NotFoundError, ValidationFailedError
from pywebfw.infrastructure.database.manager import BaseDatabaseManager
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.services.base import AuditMixin, BaseService

_BACKUP_NAME_RE = re.compile(r"^backup-[0-9-]+\.db$")


class BackupService(BaseService, AuditMixin):
    def __init__(self, db: BaseDatabaseManager, db_path: str,
                 logs: LogRepository, keep: int = 7) -> None:
        super().__init__()
        self._db = db
        self._db_path = db_path
        self._audit_repo = logs
        self._keep = keep

    @property
    def backup_dir(self) -> Path:
        return Path(self._db_path).parent / "backups"

    @property
    def supported(self) -> bool:
        return self._db.dialect == "sqlite" and self._db_path != ":memory:"

    def create(self, actor: str = "system") -> dict[str, Any]:
        if not self.supported:
            raise ConflictError(
                "Online backup is only available for file-based SQLite "
                "(use pg_dump / managed backups for other engines)")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        target = self.backup_dir / f"backup-{stamp}.db"
        escaped = str(target).replace("'", "''")
        self._db.execute(f"VACUUM INTO '{escaped}'")
        removed = self._rotate()
        self._audit(actor, "backup.created", target=target.name,
                    detail=f"rotated {removed} old backup(s)")
        size = target.stat().st_size
        return {"name": target.name, "size_bytes": size, "rotated": removed}

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_dir.is_dir():
            return []
        backups = []
        for path in sorted(self.backup_dir.glob("backup-*.db"), reverse=True):
            stat = path.stat()
            created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            backups.append({"name": path.name, "size_bytes": stat.st_size,
                            "created_at": created.isoformat(timespec="seconds")})
        return backups

    def delete(self, name: str, actor: str) -> None:
        path = self.path_for(name)
        path.unlink()
        self._audit(actor, "backup.deleted", target=name, level="warning")

    def path_for(self, name: str) -> Path:
        """Strict validation — used by delete and the download endpoint."""
        if not _BACKUP_NAME_RE.match(name):
            raise ValidationFailedError("Invalid backup file name")
        path = self.backup_dir / name
        if not path.is_file():
            raise NotFoundError(f"Backup '{name}' not found")
        return path

    def _rotate(self) -> int:
        backups = sorted(self.backup_dir.glob("backup-*.db"))
        removed = 0
        for old in backups[:-self._keep]:
            old.unlink(missing_ok=True)
            removed += 1
        return removed
