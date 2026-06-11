"""Scheduler framework core.

- `Schedule` (Strategy): when is a job due? Interval & daily-time built in.
- `RetryPolicy`: declarative retry behaviour.
- `BaseSchedulerJob` (Template Method): `execute()` owns timing, retries,
  logging and result capture; concrete jobs implement only `run()`.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.core.logging import LoggerFactory
from app.domain.models import utc_now_iso


class JobStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


@dataclass(slots=True)
class JobResult:
    job_name: str
    status: JobStatus
    started_at: str
    duration_ms: float
    attempts: int
    message: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "job": self.job_name, "status": self.status.value,
            "started_at": self.started_at, "duration_ms": self.duration_ms,
            "attempts": self.attempts, "message": self.message, "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    delay_seconds: float = 2.0


class Schedule(ABC):
    """Strategy: decides whether a job is due."""

    @abstractmethod
    def is_due(self, last_run_monotonic: float | None, now_monotonic: float) -> bool: ...

    @abstractmethod
    def describe(self) -> str: ...


@dataclass(frozen=True, slots=True)
class IntervalSchedule(Schedule):
    seconds: float

    def is_due(self, last_run_monotonic: float | None, now_monotonic: float) -> bool:
        if last_run_monotonic is None:
            return True
        return (now_monotonic - last_run_monotonic) >= self.seconds

    def describe(self) -> str:
        return f"every {self.seconds:g}s"


@dataclass(frozen=True, slots=True)
class DailyTimeSchedule(Schedule):
    """Runs once per day at HH:MM (server local time)."""

    hour: int
    minute: int = 0
    _day_seconds: float = field(default=86400.0, repr=False)

    def is_due(self, last_run_monotonic: float | None, now_monotonic: float) -> bool:
        now = datetime.now()
        past_target = (now.hour, now.minute) >= (self.hour, self.minute)
        if last_run_monotonic is None:
            return past_target
        ran_today = (now_monotonic - last_run_monotonic) < self._day_seconds
        return past_target and not ran_today

    def describe(self) -> str:
        return f"daily at {self.hour:02d}:{self.minute:02d}"


class BaseSchedulerJob(ABC):
    """Every scheduled task derives from this. Subclasses set `name`,
    `schedule`, optionally `retry_policy`, and implement `run()`."""

    name: str = "base-job"
    schedule: Schedule = IntervalSchedule(60)
    retry_policy: RetryPolicy = RetryPolicy()

    def __init__(self) -> None:
        self._logger = LoggerFactory.get(f"job.{self.name}")

    @abstractmethod
    def run(self) -> str:
        """Does the work (sync; executed in a worker thread).
        Returns a human-readable result message; raises on failure."""

    async def execute(self) -> JobResult:
        """Fixed pipeline: retries + timing + logging (Template Method)."""
        started_iso = utc_now_iso()
        started = time.perf_counter()
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            attempts = attempt
            try:
                message = await asyncio.to_thread(self.run)
                duration = round((time.perf_counter() - started) * 1000, 2)
                self._logger.info("job succeeded", job=self.name, attempts=attempts,
                                  duration_ms=duration)
                return JobResult(self.name, JobStatus.SUCCESS, started_iso,
                                 duration, attempts, message=message)
            except Exception as exc:  # job isolation: never kill the engine
                last_error = exc
                self._logger.warning("job attempt failed", job=self.name,
                                     attempt=attempt, error=str(exc))
                if attempt < self.retry_policy.max_attempts:
                    await asyncio.sleep(self.retry_policy.delay_seconds)
        duration = round((time.perf_counter() - started) * 1000, 2)
        return JobResult(self.name, JobStatus.FAILED, started_iso, duration,
                         attempts, error=str(last_error))
