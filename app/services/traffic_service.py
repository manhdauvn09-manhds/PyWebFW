"""Traffic analytics: page-view counting + online-visitor tracking.

Design: the middleware records into an in-memory, thread-safe accumulator
(`TrafficTracker`) — zero DB writes on the request path. Counters are flushed
to the database in batches: time-gated from the middleware itself (works in
every deployment topology) and additionally by `TrafficFlushJob`.

Unique visitors are estimated per day from sha256(ip|user-agent|day) hashes —
raw IPs are never stored.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.repositories.traffic_repository import TrafficRepository
from app.services.base import BaseService

ONLINE_WINDOW_SECONDS = 300
MAX_TRACKED_PATHS = 1000          # overflow paths collapse into "(other)"


def today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class TrafficTracker:
    """Thread-safe in-memory accumulator for hits, uniques and presence."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], int] = {}        # (day, path) -> hits
        self._uniques: dict[str, set[str]] = {}            # day -> visitor hashes
        self._online: dict[str, float] = {}                # visitor hash -> last seen

    def record(self, path: str, visitor_hash: str, day: str) -> None:
        with self._lock:
            key = (day, path)
            if key not in self._hits and len(self._hits) >= MAX_TRACKED_PATHS:
                key = (day, "(other)")
            self._hits[key] = self._hits.get(key, 0) + 1
            self._uniques.setdefault(day, set()).add(visitor_hash)
            self._online[visitor_hash] = time.monotonic()

    def online_count(self, window_seconds: int = ONLINE_WINDOW_SECONDS) -> int:
        floor = time.monotonic() - window_seconds
        with self._lock:
            self._online = {k: t for k, t in self._online.items() if t > floor}
            return len(self._online)

    def drain(self) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
        """Returns (pending hits, unique counts per day) and resets hit
        counters. Unique sets persist for the current day (they estimate a
        daily total) — older days are dropped to bound memory."""
        with self._lock:
            hits, self._hits = self._hits, {}
            uniques = {day: len(hashes) for day, hashes in self._uniques.items()}
            current = today_utc()
            self._uniques = {d: h for d, h in self._uniques.items() if d == current}
            return hits, uniques


class TrafficService(BaseService):
    FLUSH_INTERVAL_SECONDS = 60.0

    def __init__(self, repository: TrafficRepository) -> None:
        super().__init__()
        self._repo = repository
        self._tracker = TrafficTracker()
        self._last_flush = time.monotonic()
        self._flush_lock = threading.Lock()

    # --- request path (memory only) -------------------------------------------
    def record_request(self, path: str, visitor_hash: str) -> None:
        self._tracker.record(path, visitor_hash, today_utc())

    def online_count(self) -> int:
        return self._tracker.online_count()

    # --- persistence -------------------------------------------------------------
    def maybe_flush(self, force: bool = False) -> int:
        """Writes pending counters to the DB if the interval elapsed (or
        forced). Returns the number of rows written."""
        with self._flush_lock:
            if not force and time.monotonic() - self._last_flush < self.FLUSH_INTERVAL_SECONDS:
                return 0
            self._last_flush = time.monotonic()
            hits, uniques = self._tracker.drain()
        written = 0
        for (day, path), count in hits.items():
            self._repo.add_hits(day, path, count)
            written += 1
        for day, count in uniques.items():
            if count:
                self._repo.record_uniques(day, count)
                written += 1
        if written:
            self._logger.debug("traffic flushed", rows=written)
        return written

    # --- reporting ---------------------------------------------------------------
    def dashboard_stats(self) -> dict[str, Any]:
        self.maybe_flush(force=True)
        return {
            "online": self.online_count(),
            "today_hits": self._repo.hits_for_day(today_utc()),
            "series": self._repo.daily_series(days=7),
            "top_pages": self._repo.top_pages(days=7, limit=10),
        }
