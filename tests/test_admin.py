"""Admin auth, RBAC, user CRUD, admin pages."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_admin_pages_redirect_when_anonymous(client: TestClient) -> None:
    client.cookies.clear()
    response = client.get("/admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


def test_login_failure_is_sanitized(client: TestClient) -> None:
    response = client.post("/api/admin/auth/login",
                           json={"username": "admin", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid username or password"


def test_login_success_sets_cookie_and_opens_admin(client: TestClient) -> None:
    response = client.post("/api/admin/auth/login",
                           json={"username": "admin", "password": "ChangeMe!123"})
    assert response.status_code == 200
    assert "admin_token" in response.cookies
    page = client.get("/admin")
    assert page.status_code == 200 and "Administration" in page.text
    for path in ("/admin/dashboard", "/admin/users", "/admin/menus",
                 "/admin/logs", "/admin/db-connections"):
        assert client.get(path).status_code == 200, path


def test_admin_api_requires_token(client: TestClient) -> None:
    client.cookies.clear()
    response = client.get("/api/admin/users")
    assert response.status_code == 401


def test_user_crud_flow(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/users", headers=auth_headers, json={
        "username": "editor1", "email": "editor1@example.com",
        "password": "Secret!123", "role": "editor",
    })
    assert created.status_code == 201, created.text
    user_id = created.json()["data"]["id"]
    assert "password_hash" not in created.json()["data"]

    duplicate = client.post("/api/admin/users", headers=auth_headers, json={
        "username": "editor1", "email": "other@example.com", "password": "Secret!123",
    })
    assert duplicate.status_code == 409

    updated = client.put(f"/api/admin/users/{user_id}", headers=auth_headers, json={
        "username": "editor1", "email": "editor1@example.com", "role": "admin",
    })
    assert updated.json()["data"]["role"] == "admin"

    listing = client.get("/api/admin/users", headers=auth_headers,
                         params={"sort_by": "username"}).json()
    assert listing["meta"]["total"] >= 2

    bad_sort = client.get("/api/admin/users", headers=auth_headers,
                          params={"sort_by": "password_hash; DROP TABLE users"})
    assert bad_sort.status_code == 422   # ORDER BY whitelist blocks injection

    deleted = client.delete(f"/api/admin/users/{user_id}", headers=auth_headers)
    assert deleted.json()["data"]["deleted"] == user_id


def test_non_admin_role_is_rejected(client: TestClient, auth_headers: dict[str, str]) -> None:
    client.post("/api/admin/users", headers=auth_headers, json={
        "username": "viewer1", "email": "viewer1@example.com",
        "password": "Secret!123", "role": "viewer",
    })
    login = client.post("/api/admin/auth/login",
                        json={"username": "viewer1", "password": "Secret!123"})
    viewer_token = login.json()["data"]["token"]
    response = client.get("/api/admin/users",
                          headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 403
    client.cookies.clear()


def test_dashboard_metrics_and_audit_trail(client: TestClient,
                                           auth_headers: dict[str, str]) -> None:
    metrics = client.get("/api/admin/dashboard/metrics", headers=auth_headers).json()
    assert metrics["data"]["counts"]["users"] >= 1
    assert metrics["data"]["database"]["healthy"] is True

    logs = client.get("/api/admin/logs", headers=auth_headers).json()
    actions = [entry["action"] for entry in logs["data"]]
    assert "login.success" in actions or "user.created" in actions
