"""Scheduler engine: registry + async runner + result tracking."""
from __future__ import annotations

import asyncio
import time

from app.core.events import EventBus
from app.core.exceptions import SchedulerError
from app.core.logging import LoggerFactory
from app.domain.models import AuditLog
from app.repositories.log_repository import LogRepository
from app.scheduler.base import BaseSchedulerJob, JobResult, JobStatus


class JobRegistry:
    """Holds all registered jobs; extension point for new scheduled tasks."""

    def __init__(self) -> None:
        self._jobs: dict[str, BaseSchedulerJob] = {}

    def register(self, job: BaseSchedulerJob) -> "JobRegistry":
        if job.name in self._jobs:
            raise SchedulerError(f"Duplicate job name: {job.name}")
        self._jobs[job.name] = job
        return self

    def get(self, name: str) -> BaseSchedulerJob:
        if name not in self._jobs:
            raise SchedulerError(f"Unknown job: {name}")
        return self._jobs[name]

    def all(self) -> list[BaseSchedulerJob]:
        return list(self._jobs.values())


class SchedulerEngine:
    """Async loop that fires due jobs concurrently and tracks their results."""

    def __init__(self, registry: JobRegistry, tick_seconds: float,
                 audit_logs: LogRepository | None = None,
                 events: EventBus | None = None) -> None:
        self._registry = registry
        self._tick = tick_seconds
        self._audit_logs = audit_logs
        self._events = events
        self._logger = LoggerFactory.get("scheduler")
        self._last_run: dict[str, float] = {}
        self._last_result: dict[str, JobResult] = {}
        self._task: asyncio.Task | None = None
        self._running: set[asyncio.Task] = set()
        self._stopping = asyncio.Event()

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        self._logger.info("scheduler started",
                          jobs=[j.name for j in self._registry.all()])

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stopping.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        if self._running:   # let in-flight jobs finish cleanly
            await asyncio.gather(*list(self._running), return_exceptions=True)
        self._logger.info("scheduler stopped")

    # --- core loop -----------------------------------------------------------
    async def _loop(self) -> None:
        while not self._stopping.is_set():
            now = time.monotonic()
            due = [job for job in self._registry.all()
                   if job.schedule.is_due(self._last_run.get(job.name), now)]
            for job in due:
                self._last_run[job.name] = now
                # Fire-and-track: a slow job must never delay other jobs' ticks.
                task = asyncio.create_task(self._run_and_record(job),
                                           name=f"job-{job.name}")
                self._running.add(task)
                task.add_done_callback(self._running.discard)
            await asyncio.sleep(self._tick)

    async def _run_and_record(self, job: BaseSchedulerJob) -> None:
        self._record(await job.execute())

    async def run_job_now(self, name: str) -> JobResult:
        """Manual trigger (used by admin tooling/tests)."""
        result = await self._registry.get(name).execute()
        self._last_run[name] = time.monotonic()
        self._record(result)
        return result

    @property
    def registry(self) -> JobRegistry:
        return self._registry

    def _record(self, result: JobResult) -> None:
        self._last_result[result.job_name] = result
        if result.status is JobStatus.FAILED and self._events is not None:
            # Subscribers (e.g. an email alert) react in bootstrap wiring.
            self._events.publish("job.failed", result.to_dict())
        if self._audit_logs is not None:
            level = "info" if result.status is JobStatus.SUCCESS else "error"
            try:
                self._audit_logs.add(AuditLog(
                    actor="scheduler", action=f"job.{result.status.value}",
                    target=result.job_name,
                    detail=result.message or result.error, level=level))
            except Exception:
                self._logger.warning("failed to persist job audit log",
                                     job=result.job_name)

    @property
    def status_report(self) -> list[dict]:
        report = []
        for job in self._registry.all():
            last = self._last_result.get(job.name)
            report.append({
                "job": job.name,
                "schedule": job.schedule.describe(),
                "last_result": last.to_dict() if last else None,
            })
        return report
