from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import ApplicationBuilder
from app.config.settings import (
    KNOWN_MODULES,
    AppSettings,
    CacheSettings,
    DatabaseSettings,
    MediaSettings,
    RateLimitSettings,
    SchedulerSettings,
    SecuritySettings,
)


def build_test_settings(db_path: str,
                        modules: frozenset[str] = KNOWN_MODULES,
                        environment: str = "test",
                        login_max_requests: int = 1_000) -> AppSettings:
    return AppSettings(
        modules=modules,
        # media files land next to the test DB, never in the repo tree
        media=MediaSettings(dir=str(Path(db_path).parent / "media"), max_upload_mb=1),
        name="TestApp",
        environment=environment,
        debug=True,
        host="127.0.0.1",
        port=8000,
        database=DatabaseSettings(path=db_path, pool_size=3, idle_timeout_seconds=60),
        security=SecuritySettings(
            secret_key="test-secret-key",
            token_ttl_seconds=600,
            # low iteration count: keep the test suite fast, never use in prod
            password_iterations=1_000,
        ),
        cache=CacheSettings(default_ttl_seconds=60),
        scheduler=SchedulerSettings(enabled=True, tick_seconds=1),
        rate_limit=RateLimitSettings(
            max_requests=1_000, window_seconds=60,
            login_max_requests=login_max_requests, login_window_seconds=60,
        ),
    )


def unlock_seed_admin(test_client: TestClient) -> None:
    """The seed admin boots with must_change_password=1; complete the forced
    password-change flow once so the rest of the suite can operate normally
    (the dedicated P2 tests use fresh apps to test the flow itself)."""
    login = test_client.post("/api/admin/auth/login",
                             json={"username": "admin", "password": "ChangeMe!123"})
    assert login.status_code == 200, login.text
    token = login.json()["data"]["token"]
    changed = test_client.post(
        "/api/admin/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "ChangeMe!123", "new_password": "ChangeMe!123"},
    )
    assert changed.status_code == 200, changed.text
    test_client.cookies.clear()


@pytest.fixture(scope="session")
def client(tmp_path_factory: pytest.TempPathFactory):
    db_path = str(tmp_path_factory.mktemp("db") / "test.db")
    app = ApplicationBuilder(build_test_settings(db_path)).build_app()
    with TestClient(app, follow_redirects=False) as test_client:
        unlock_seed_admin(test_client)
        yield test_client


@pytest.fixture(scope="session")
def admin_token(client: TestClient) -> str:
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "ChangeMe!123"},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["token"]


@pytest.fixture()
def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}
