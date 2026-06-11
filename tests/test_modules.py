"""Module isolation: the same codebase deploys as FE-only, admin-only, or
scheduler-only processes (APP_MODULES), mirroring the split Docker setup."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.bootstrap import ApplicationBuilder
from tests.conftest import build_test_settings


def _client(tmp_path, modules: frozenset[str]) -> TestClient:
    settings = build_test_settings(str(tmp_path / "mod.db"), modules=modules)
    return TestClient(ApplicationBuilder(settings).build_app(), follow_redirects=False)


def test_fe_only_container_has_no_admin_surface(tmp_path) -> None:
    with _client(tmp_path, frozenset({"public"})) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/public/menus").json()["success"] is True
        # No admin routes mounted at all — not even the login screen.
        assert client.get("/admin").status_code == 404
        assert client.get("/admin/login").status_code == 404
        assert client.post("/api/admin/auth/login",
                           json={"username": "a", "password": "b"}).status_code == 404
        health = client.get("/healthz").json()
        assert health["modules"] == ["public"]
        assert "scheduler" not in health


def test_admin_only_container_has_no_public_site(tmp_path) -> None:
    with _client(tmp_path, frozenset({"admin"})) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/public/menus").status_code == 404
        assert client.get("/admin").status_code == 303  # redirect to login
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        assert login.status_code == 200


def test_scheduler_only_container_runs_jobs_without_web_routes(tmp_path) -> None:
    with _client(tmp_path, frozenset({"scheduler"})) as client:
        assert client.get("/").status_code == 404
        assert client.get("/admin").status_code == 404
        health = client.get("/healthz").json()
        assert health["status"] == "ok"
        assert health["modules"] == ["scheduler"]
        jobs = [entry["job"] for entry in health["scheduler"]]
        assert "database-health-check" in jobs and "cache-warmup" in jobs


def test_unknown_module_is_rejected(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Unknown APP_MODULES"):
        build_test_settings(str(tmp_path / "x.db"), modules=frozenset({"publicc"}))
