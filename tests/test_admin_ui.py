"""Group A polish: user/menu form editors, pagination, job-failure events."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.core.events import EventBus
from app.scheduler.base import (
    BaseSchedulerJob,
    IntervalSchedule,
    JobStatus,
    RetryPolicy,
)


def _login(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})


def test_user_management_form_editor(client: TestClient) -> None:
    _login(client)
    listing = client.get("/admin/users")
    assert listing.status_code == 200
    assert "+ New user" in listing.text

    new_form = client.get("/admin/users", params={"new": "1"})
    assert 'id="user-form"' in new_form.text
    assert '<select name="role">' in new_form.text

    edit_form = client.get("/admin/users", params={"edit": "1"})
    assert 'data-user-id="1"' in edit_form.text
    assert "leave blank to keep current" in edit_form.text
    client.cookies.clear()


def test_menu_management_form_editor(client: TestClient) -> None:
    _login(client)
    new_form = client.get("/admin/menus", params={"new": "1"})
    assert 'id="menu-form"' in new_form.text
    edit_form = client.get("/admin/menus", params={"edit": "1"})
    assert 'data-menu-id="1"' in edit_form.text
    client.cookies.clear()


def test_pagination_renders_and_tolerates_bad_input(client: TestClient,
                                                    auth_headers: dict[str, str]) -> None:
    _login(client)
    # The session DB has accumulated plenty of audit logs -> multiple pages.
    total = client.get("/api/admin/logs", headers=auth_headers).json()["meta"]["total"]
    page1 = client.get("/admin/logs")
    if total > 50:
        assert 'class="pagination"' in page1.text
        assert "/admin/logs?page=2" in page1.text
        assert client.get("/admin/logs", params={"page": "2"}).status_code == 200
    # Garbage page numbers fall back to page 1, never crash.
    assert client.get("/admin/logs", params={"page": "abc"}).status_code == 200
    assert client.get("/admin/users", params={"page": "-5"}).status_code == 200
    client.cookies.clear()


class AlwaysFailingJob(BaseSchedulerJob):
    name = "always-fails"
    schedule = IntervalSchedule(999_999)
    retry_policy = RetryPolicy(max_attempts=1, delay_seconds=0)

    def run(self) -> str:
        raise RuntimeError("intentional test failure")


def test_failed_job_publishes_event_and_audits(tmp_path) -> None:
    # Fresh app: never pollute the session-scoped engine's registry.
    from app.bootstrap import ApplicationBuilder
    from tests.conftest import build_test_settings, unlock_seed_admin

    settings = build_test_settings(str(tmp_path / "jobfail.db"))
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app, follow_redirects=False) as client:
        unlock_seed_admin(client)
        engine = app.state.scheduler_engine
        bus = app.state.container.resolve(EventBus)
        received = []
        bus.subscribe("job.failed", received.append)
        engine.registry.register(AlwaysFailingJob())

        result = asyncio.run(engine.run_job_now("always-fails"))
        assert result.status is JobStatus.FAILED

        # Event published with the failure details...
        assert received and received[0].payload["job"] == "always-fails"
        assert "intentional test failure" in received[0].payload["error"]
        # ...and the audit trail recorded it as an error.
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}
        logs = client.get("/api/admin/logs", headers=headers,
                          params={"level": "error"}).json()
        assert any(entry["target"] == "always-fails" for entry in logs["data"])
