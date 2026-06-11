"""Media file storage.

`BaseMediaStorage` is the contract (an S3/MinIO implementation can replace it
later); `LocalMediaStorage` stores files on disk under the configured root.
Stored names are framework-generated (random hex + extension), so the storage
layer never handles user-controlled paths.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredFile:
    name: str
    size_bytes: int
    modified_at: str


class BaseMediaStorage(ABC):
    @abstractmethod
    def save(self, name: str, data: bytes) -> StoredFile: ...

    @abstractmethod
    def list_files(self) -> list[StoredFile]: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...

    @abstractmethod
    def exists(self, name: str) -> bool: ...

    @abstractmethod
    def path_of(self, name: str) -> Path:
        """Filesystem path for serving (local storage only)."""


class LocalMediaStorage(BaseMediaStorage):
    def __init__(self, root: str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _stat(self, path: Path) -> StoredFile:
        stat = path.stat()
        modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return StoredFile(name=path.name, size_bytes=stat.st_size,
                          modified_at=modified.isoformat(timespec="seconds"))

    def save(self, name: str, data: bytes) -> StoredFile:
        path = self._root / name
        path.write_bytes(data)
        return self._stat(path)

    def list_files(self) -> list[StoredFile]:
        files = [self._stat(p) for p in self._root.iterdir() if p.is_file()]
        return sorted(files, key=lambda f: f.modified_at, reverse=True)

    def delete(self, name: str) -> None:
        (self._root / name).unlink(missing_ok=True)

    def exists(self, name: str) -> bool:
        return (self._root / name).is_file()

    def path_of(self, name: str) -> Path:
        return self._root / name
