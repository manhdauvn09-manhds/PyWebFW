"""Scheduler engine + jobs run for real against the test database."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from pywebfw.scheduler.base import JobStatus
from pywebfw.scheduler.engine import SchedulerEngine


def _engine(client: TestClient) -> SchedulerEngine:
    return client.app.state.scheduler_engine


def test_all_registered_jobs_run_successfully(client: TestClient) -> None:
    engine = _engine(client)
    for entry in engine.status_report:
        result = asyncio.run(engine.run_job_now(entry["job"]))
        assert result.status is JobStatus.SUCCESS, f"{entry['job']}: {result.error}"


def test_job_results_are_tracked_and_audited(client: TestClient) -> None:
    engine = _engine(client)
    asyncio.run(engine.run_job_now("database-health-check"))
    report = {entry["job"]: entry for entry in engine.status_report}
    last = report["database-health-check"]["last_result"]
    assert last["status"] == "success"
    assert "latency" in last["message"]
