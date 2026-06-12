"""Database access layer.

- `ConnectionPool`: bounded pool with idle-timeout eviction.
- `BaseDatabaseManager` (ABC): the only DB contract repositories know about.
- `PooledDatabaseManager`: shared pool + ambient-transaction machinery.
- `SQLiteDatabaseManager`: default implementation (zero external deps).
- `PostgresDatabaseManager`: same contract over psycopg3 — switch with
  DB_DRIVER=postgres + DB_DSN, nothing above this layer changes.

Transactions use a `contextvars` ambient connection: inside
`manager.transaction()` every execute/fetch on any repository reuses the same
connection, giving a transparent Unit of Work without threading connection
objects through every method signature.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Sequence

from pywebfw.core.exceptions import ConflictError, DatabaseError
from pywebfw.core.logging import BaseLogger


class PooledConnection:
    """Wraps a raw connection with bookkeeping for the idle-close policy."""

    def __init__(self, raw) -> None:
        self.raw = raw
        self.last_used = time.monotonic()

    def touch(self) -> None:
        self.last_used = time.monotonic()

    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


class ConnectionPool:
    """Thread-safe bounded connection pool with idle eviction."""

    def __init__(self, factory, max_size: int = 5) -> None:
        self._factory = factory
        self._max_size = max_size
        self._idle: list[PooledConnection] = []
        self._in_use = 0
        self._lock = threading.Condition()

    @contextlib.contextmanager
    def acquire(self, timeout: float = 10.0) -> Iterator[PooledConnection]:
        conn = self._checkout(timeout)
        try:
            yield conn
        finally:
            self._checkin(conn)

    def _checkout(self, timeout: float) -> PooledConnection:
        deadline = time.monotonic() + timeout
        with self._lock:
            while True:
                if self._idle:
                    conn = self._idle.pop()
                    self._in_use += 1
                    return conn
                if self._in_use < self._max_size:
                    self._in_use += 1
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._lock.wait(remaining):
                    raise DatabaseError("Connection pool exhausted")
        try:
            return PooledConnection(self._factory())
        except Exception:
            with self._lock:
                self._in_use -= 1
                self._lock.notify()
            raise

    def _checkin(self, conn: PooledConnection) -> None:
        conn.touch()
        with self._lock:
            self._idle.append(conn)
            self._in_use -= 1
            self._lock.notify()

    def close_idle(self, max_idle_seconds: float) -> int:
        """Closes connections idle longer than the threshold. Returns count."""
        closed = 0
        with self._lock:
            keep: list[PooledConnection] = []
            for conn in self._idle:
                if conn.idle_seconds() > max_idle_seconds:
                    conn.raw.close()
                    closed += 1
                else:
                    keep.append(conn)
            self._idle = keep
        return closed

    def close_all(self) -> None:
        with self._lock:
            for conn in self._idle:
                conn.raw.close()
            self._idle.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"idle": len(self._idle), "in_use": self._in_use, "max": self._max_size}


class BaseDatabaseManager(ABC):
    """Contract every repository depends on (DIP — no concrete driver leaks up)."""

    @property
    @abstractmethod
    def dialect(self) -> str:
        """'sqlite' | 'postgres' — for the rare engine-specific SQL path."""

    @abstractmethod
    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None: ...

    @abstractmethod
    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Runs a write statement; returns lastrowid (insert) or rowcount."""

    @abstractmethod
    def transaction(self) -> contextlib.AbstractContextManager[None]:
        """All statements inside share one connection; commit/rollback at exit."""

    @abstractmethod
    def health_check(self) -> dict[str, Any]: ...

    @abstractmethod
    def optimize(self) -> dict[str, Any]:
        """Engine-specific maintenance (ANALYZE, reindex, ...)."""

    @abstractmethod
    def close_idle_connections(self, max_idle_seconds: float) -> int: ...

    @abstractmethod
    def shutdown(self) -> None: ...


class PooledDatabaseManager(BaseDatabaseManager, ABC):
    """Shared machinery: pool, ambient transaction, health, idle policy.
    Subclasses provide the driver specifics (connection factory, param style,
    insert-id strategy, maintenance commands, error types)."""

    def __init__(self, pool_size: int, logger: BaseLogger) -> None:
        self._logger = logger
        self._errors = self._driver_error_types()
        self._integrity = self._integrity_error_types()
        self._pool = ConnectionPool(self._create_connection, max_size=pool_size)
        self._tx_conn: ContextVar[PooledConnection | None] = ContextVar("tx_conn", default=None)

    # --- driver hooks ---------------------------------------------------------
    @abstractmethod
    def _create_connection(self): ...

    @abstractmethod
    def _driver_error_types(self) -> tuple[type[BaseException], ...]: ...

    @abstractmethod
    def _integrity_error_types(self) -> tuple[type[BaseException], ...]:
        """Constraint-violation errors (UNIQUE, FK, CHECK) — mapped to 409."""

    @abstractmethod
    def _execute_write(self, conn: PooledConnection, sql: str,
                       params: tuple, is_insert: bool) -> int: ...

    def _translate(self, sql: str) -> str:
        """Adapts the framework's '?' placeholder style to the driver's."""
        return sql

    # --- shared implementation --------------------------------------------------
    @contextlib.contextmanager
    def _connection(self) -> Iterator[PooledConnection]:
        ambient = self._tx_conn.get()
        if ambient is not None:           # inside transaction(): reuse it
            yield ambient
            return
        with self._pool.acquire() as conn:
            yield conn

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        try:
            with self._connection() as conn:
                cursor = conn.raw.execute(self._translate(sql), tuple(params))
                return [dict(row) for row in cursor.fetchall()]
        except self._errors as exc:
            # Driver detail (table/column names) goes to the log only —
            # clients get a sanitized message.
            self._logger.error("query failed", error=str(exc))
            raise DatabaseError("Database query failed") from exc

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        is_insert = sql.lstrip().upper().startswith("INSERT")
        try:
            with self._connection() as conn:
                result = self._execute_write(conn, self._translate(sql),
                                             tuple(params), is_insert)
                if self._tx_conn.get() is None:
                    conn.raw.commit()
                return result
        except self._integrity as exc:
            # Lost race on a UNIQUE/FK constraint: a client error (409),
            # not a server failure — and no schema details leak out.
            self._logger.warning("integrity violation", error=str(exc))
            raise ConflictError("Conflict: duplicate value or invalid reference") from exc
        except self._errors as exc:
            self._logger.error("statement failed", error=str(exc))
            raise DatabaseError("Database operation failed") from exc

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        if self._tx_conn.get() is not None:   # nested: join outer transaction
            yield
            return
        with self._pool.acquire() as conn:
            token = self._tx_conn.set(conn)
            try:
                yield
                conn.raw.commit()
            except Exception:
                conn.raw.rollback()
                raise
            finally:
                self._tx_conn.reset(token)

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            self.fetch_one("SELECT 1 AS ok")
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return {"healthy": True, "latency_ms": latency_ms, "pool": self._pool.stats}
        except DatabaseError as exc:
            return {"healthy": False, "error": exc.message, "pool": self._pool.stats}

    def close_idle_connections(self, max_idle_seconds: float) -> int:
        closed = self._pool.close_idle(max_idle_seconds)
        if closed:
            self._logger.info("closed idle db connections", count=closed)
        return closed

    def shutdown(self) -> None:
        self._pool.close_all()


class SQLiteDatabaseManager(PooledDatabaseManager):
    """SQLite implementation — the zero-dependency default."""

    def __init__(self, db_path: str, pool_size: int, logger: BaseLogger) -> None:
        self._path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # :memory: must share a single connection or each one sees an empty DB.
        effective_size = 1 if db_path == ":memory:" else pool_size
        super().__init__(effective_size, logger)

    @property
    def dialect(self) -> str:
        return "sqlite"

    def _driver_error_types(self) -> tuple[type[BaseException], ...]:
        return (sqlite3.Error,)

    def _integrity_error_types(self) -> tuple[type[BaseException], ...]:
        return (sqlite3.IntegrityError,)

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        # Multiple processes/containers may share the DB file: wait for locks
        # instead of failing immediately.
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _execute_write(self, conn: PooledConnection, sql: str,
                       params: tuple, is_insert: bool) -> int:
        cursor = conn.raw.execute(sql, params)
        return cursor.lastrowid if is_insert else cursor.rowcount

    def optimize(self) -> dict[str, Any]:
        started = time.perf_counter()
        with self._pool.acquire() as conn:
            conn.raw.execute("ANALYZE")
            conn.raw.execute("PRAGMA optimize")
            conn.raw.commit()
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        self._logger.info("database optimized", duration_ms=duration_ms)
        return {"optimized": True, "duration_ms": duration_ms}
