"""Caching layer.

`BaseCacheManager` is the contract services use; `InMemoryCacheManager` is the
default. The Redis backend for multi-instance deployments ships with
PyWebFW Pro — same ABC, zero service changes.
"""
from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at


class BaseCacheManager(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def stats(self) -> dict[str, Any]: ...

    def get_or_set(self, key: str, loader: Callable[[], Any], ttl_seconds: int | None = None) -> Any:
        """Read-through helper shared by all implementations."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = loader()
        if value is not None:
            self.set(key, value, ttl_seconds)
        return value


class InMemoryCacheManager(BaseCacheManager):
    def __init__(self, default_ttl_seconds: int = 120) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._key_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None or entry.is_expired:
                if entry is not None:
                    del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        with self._lock:
            self._store[key] = CacheEntry(value=value, expires_at=time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def purge_expired(self) -> int:
        with self._lock:
            keys = [k for k, v in self._store.items() if v.is_expired]
            for k in keys:
                del self._store[k]
            return len(keys)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"entries": len(self._store), "hits": self._hits, "misses": self._misses}

    def get_or_set(self, key: str, loader: Callable[[], Any], ttl_seconds: int | None = None) -> Any:
        """Override with per-key locking to prevent thundering-herd on cold keys."""
        # Fast path: key already warm.
        val = self.get(key)
        if val is not None:
            return val
        # Acquire a per-key lock so only one thread calls loader().
        with self._locks_lock:
            if key not in self._key_locks:
                self._key_locks[key] = threading.Lock()
            key_lock = self._key_locks[key]
        with key_lock:
            val = self.get(key)
            if val is not None:
                return val
            val = loader()
            if val is not None:
                self.set(key, val, ttl_seconds)
            return val
