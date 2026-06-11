"""Aggregated traffic statistics. Standalone (not BaseRepository) because the
access pattern is upsert/aggregate, not entity CRUD. Upserts are portable
(ON CONFLICT ... DO UPDATE works on SQLite ≥3.24 and PostgreSQL)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.domain.models import utc_now_iso
from app.infrastructure.database.manager import BaseDatabaseManager


class TrafficRepository:
    def __init__(self, db: BaseDatabaseManager) -> None:
        self._db = db

    def add_hits(self, day: str, path: str, hits: int) -> None:
        now = utc_now_iso()
        self._db.execute(
            "INSERT INTO traffic_stats (day, path, hits, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(day, path) DO UPDATE SET"
            " hits = traffic_stats.hits + excluded.hits,"
            " updated_at = excluded.updated_at",
            (day, path, hits, now, now),
        )

    def record_uniques(self, day: str, uniques: int) -> None:
        """Stores the day's unique-visitor estimate (monotonic max — portable
        CASE expression instead of engine-specific MAX/GREATEST)."""
        now = utc_now_iso()
        self._db.execute(
            "INSERT INTO traffic_daily (day, uniques, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(day) DO UPDATE SET"
            " uniques = CASE WHEN excluded.uniques > traffic_daily.uniques"
            "           THEN excluded.uniques ELSE traffic_daily.uniques END,"
            " updated_at = excluded.updated_at",
            (day, uniques, now, now),
        )

    def daily_series(self, days: int = 7) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        return self._db.fetch_all(
            "SELECT s.day AS day, SUM(s.hits) AS hits, COALESCE(MAX(u.uniques), 0) AS uniques"
            " FROM traffic_stats s LEFT JOIN traffic_daily u ON u.day = s.day"
            " WHERE s.day >= ? GROUP BY s.day ORDER BY s.day",
            (since,),
        )

    def top_pages(self, days: int = 7, limit: int = 10) -> list[dict[str, Any]]:
        since = (date.today() - timedelta(days=days - 1)).isoformat()
        return self._db.fetch_all(
            "SELECT path, SUM(hits) AS hits FROM traffic_stats"
            " WHERE day >= ? GROUP BY path ORDER BY hits DESC LIMIT ?",
            (since, limit),
        )

    def hits_for_day(self, day: str) -> int:
        row = self._db.fetch_one(
            "SELECT COALESCE(SUM(hits), 0) AS n FROM traffic_stats WHERE day = ?", (day,))
        return row["n"] if row else 0
