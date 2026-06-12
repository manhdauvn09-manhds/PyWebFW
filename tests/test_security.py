"""P1 security hardening: login throttling + production cookie flags."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
from tests.conftest import build_test_settings


def test_login_endpoint_is_throttled_separately(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "throttle.db"), login_max_requests=3)
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app) as client:
        bad = {"username": "admin", "password": "wrong-password"}
        for _ in range(3):
            assert client.post("/api/admin/auth/login", json=bad).status_code == 401
        # 4th attempt within the window hits the dedicated login limiter.
        blocked = client.post("/api/admin/auth/login", json=bad)
        assert blocked.status_code == 429
        assert "login" in blocked.json()["error"]["message"].lower()
        # Other API endpoints are NOT affected by the login limiter.
        assert client.get("/api/public/menus").status_code == 200


def test_admin_cookie_is_secure_in_production(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "prod.db"), environment="production")
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app) as client:
        response = client.post("/api/admin/auth/login",
                               json={"username": "admin", "password": "ChangeMe!123"})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"].lower()
        assert "httponly" in cookie
        assert "samesite=strict" in cookie
        assert "secure" in cookie          # only set when is_production


def test_admin_cookie_not_secure_in_dev(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "dev.db"))
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app) as client:
        response = client.post("/api/admin/auth/login",
                               json={"username": "admin", "password": "ChangeMe!123"})
        cookie = response.headers["set-cookie"].lower()
        assert "secure" not in cookie      # local HTTP dev must still work
