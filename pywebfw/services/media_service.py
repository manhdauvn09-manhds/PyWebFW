"""Media uploads: extension whitelist, size cap, framework-generated stored
names (random hex) — user-controlled filenames never touch the filesystem."""
from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any

from pywebfw.core.exceptions import NotFoundError, ValidationFailedError
from pywebfw.infrastructure.media.storage import BaseMediaStorage
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.services.base import AuditMixin, BaseService

ALLOWED_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "gif", "webp", "pdf"})
# Stored names are always <16 hex chars>.<ext> — anything else is rejected,
# which also makes path traversal structurally impossible.
_STORED_NAME_RE = re.compile(r"^[a-f0-9]{16}\.[a-z0-9]{2,5}$")


class MediaService(BaseService, AuditMixin):
    def __init__(self, storage: BaseMediaStorage, logs: LogRepository,
                 max_upload_mb: int = 5) -> None:
        super().__init__()
        self._storage = storage
        self._audit_repo = logs
        self._max_bytes = max_upload_mb * 1024 * 1024

    def save_upload(self, original_name: str, data: bytes, actor: str) -> dict[str, Any]:
        extension = Path(original_name).suffix.lstrip(".").lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise ValidationFailedError(
                f"File type '.{extension}' is not allowed "
                f"(allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))})")
        if not data:
            raise ValidationFailedError("Uploaded file is empty")
        if len(data) > self._max_bytes:
            raise ValidationFailedError(
                f"File too large (max {self._max_bytes // (1024 * 1024)} MB)")
        stored_name = f"{secrets.token_hex(8)}.{extension}"
        info = self._storage.save(stored_name, data)
        self._audit(actor, "media.uploaded", target=stored_name,
                    detail=f"original={original_name} size={info.size_bytes}")
        return self._to_dict(info)

    def list_files(self) -> list[dict[str, Any]]:
        return [self._to_dict(f) for f in self._storage.list_files()]

    def delete(self, name: str, actor: str) -> None:
        self._validate_name(name)
        if not self._storage.exists(name):
            raise NotFoundError(f"Media file '{name}' not found")
        self._storage.delete(name)
        self._audit(actor, "media.deleted", target=name, level="warning")

    def resolve_path(self, name: str) -> Path:
        """For the serving route: strict name validation + existence check."""
        self._validate_name(name)
        if not self._storage.exists(name):
            raise NotFoundError("Media file not found")
        return self._storage.path_of(name)

    @staticmethod
    def _validate_name(name: str) -> None:
        if not _STORED_NAME_RE.match(name):
            raise ValidationFailedError("Invalid media file name")

    @staticmethod
    def _to_dict(info) -> dict[str, Any]:
        return {"name": info.name, "size_bytes": info.size_bytes,
                "modified_at": info.modified_at, "url": f"/media/{info.name}"}
