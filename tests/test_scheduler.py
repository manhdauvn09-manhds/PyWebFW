"""Scheduler engine + jobs run for real against the test database."""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

from fastapi.testclient import TestClient

from pywebfw.scheduler.base import DailyTimeSchedule, IntervalSchedule, JobStatus
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


# --- DailyTimeSchedule -------------------------------------------------------
# Regression cover for a bug where a "daily at HH:MM" job actually fired every 24h
# counted from the previous run. Since the monotonic clock restarts near zero with
# the process, the first check after boot always fired, pinning the job to whatever
# time the container happened to start. On mcp-80 the 02:30 backup ran at 14:20 —
# the container's start time — every day, and drifted again on each restart.

def _at(hour: int, minute: int = 0):
    """Freeze DailyTimeSchedule's wall clock at a local time today."""
    frozen = datetime(2026, 7, 17, hour, minute)
    return patch("pywebfw.scheduler.base.datetime",
                 **{"now.return_value": frozen, "side_effect": None})


def test_daily_not_due_before_target_time() -> None:
    with _at(1, 30):
        assert DailyTimeSchedule(hour=2, minute=30).is_due(None, 1000.0) is False


def test_daily_due_at_target_when_never_run() -> None:
    with _at(2, 30):
        assert DailyTimeSchedule(hour=2, minute=30).is_due(None, 1000.0) is True


def test_daily_not_due_twice_on_the_same_day() -> None:
    # ran 10 minutes ago, i.e. today at 02:30 -> must not fire again
    with _at(2, 40):
        assert DailyTimeSchedule(hour=2, minute=30).is_due(1000.0, 1600.0) is False


def test_daily_due_again_the_next_day() -> None:
    # last run ~24h ago (yesterday 02:35), now past today's target
    with _at(2, 40):
        day = 86400.0
        assert DailyTimeSchedule(hour=2, minute=30).is_due(1000.0, 1000.0 + day) is True


def test_daily_recovers_target_after_an_off_target_run() -> None:
    """The regression that matters — this is what pinned mcp-80's 02:30 backup to
    14:20 for weeks.

    A container booting at 14:20 fires the job immediately (catch-up), so the last
    run sits at an off-target 14:20. The next 02:30 must still fire; otherwise the
    job can only ever come due 24h after 14:20, and 14:20 becomes the permanent
    schedule.

    Old code asked "has it run in the last 24h?" — at 02:30 only 12h had passed, so
    it declined to run and the drift locked in."""
    with _at(2, 31):
        # last run yesterday 14:20; now today 02:31 -> 12h11m elapsed = 43860s
        assert DailyTimeSchedule(hour=2, minute=30).is_due(0.0, 43860.0) is True


def test_interval_schedule_unaffected() -> None:
    assert IntervalSchedule(60).is_due(None, 5.0) is True
    assert IntervalSchedule(60).is_due(100.0, 130.0) is False
    assert IntervalSchedule(60).is_due(100.0, 160.0) is True
