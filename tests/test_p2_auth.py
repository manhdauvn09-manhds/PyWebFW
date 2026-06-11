"""P2: forced password change on first login + token revocation."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
from tests.conftest import build_test_settings


def _fresh_client(tmp_path, name: str) -> TestClient:
    settings = build_test_settings(str(tmp_path / f"{name}.db"))
    return TestClient(ApplicationBuilder(settings).build_app(), follow_redirects=False)


def test_forced_password_change_flow(tmp_path) -> None:
    with _fresh_client(tmp_path, "forced") as client:
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        assert login.json()["data"]["user"]["must_change_password"] is True
        token = login.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Every admin resource is blocked until the password is changed...
        blocked = client.get("/api/admin/users", headers=headers)
        assert blocked.status_code == 403
        assert blocked.json()["error"]["details"]["reason"] == "password_change_required"
        # ...and admin pages redirect to the change-password screen.
        assert client.get("/admin").headers["location"] == "/admin/change-password"
        assert client.get("/admin/change-password").status_code == 200

        # Wrong current password is rejected.
        assert client.post("/api/admin/auth/change-password", headers=headers,
                           json={"current_password": "nope!nope",
                                 "new_password": "NewSecret!456"}).status_code == 401

        changed = client.post("/api/admin/auth/change-password", headers=headers,
                              json={"current_password": "ChangeMe!123",
                                    "new_password": "NewSecret!456"})
        assert changed.status_code == 200
        assert changed.json()["data"]["user"]["must_change_password"] is False
        new_token = changed.json()["data"]["token"]

        # Old token was revoked by the version bump; the new one works.
        assert client.get("/api/admin/users", headers=headers).status_code == 401
        assert client.get("/api/admin/users",
                          headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
        # New password is live.
        client.cookies.clear()
        assert client.post("/api/admin/auth/login",
                           json={"username": "admin",
                                 "password": "NewSecret!456"}).status_code == 200


def test_logout_revokes_all_tokens(tmp_path) -> None:
    with _fresh_client(tmp_path, "logout") as client:
        from tests.conftest import unlock_seed_admin
        unlock_seed_admin(client)
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        token = login.json()["data"]["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/admin/auth/me", headers=headers).status_code == 200

        assert client.post("/api/admin/auth/logout", headers=headers).status_code == 200
        client.cookies.clear()
        # The very same bearer token is now invalid everywhere.
        assert client.get("/api/admin/auth/me", headers=headers).status_code == 401


def test_admin_password_update_revokes_sessions(tmp_path) -> None:
    with _fresh_client(tmp_path, "revoke") as client:
        from tests.conftest import unlock_seed_admin
        unlock_seed_admin(client)
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        data = login.json()["data"]
        token, user_id = data["token"], data["user"]["id"]
        headers = {"Authorization": f"Bearer {token}"}

        # Admin resets the password through the user-management API.
        updated = client.put(f"/api/admin/users/{user_id}", headers=headers, json={
            "username": "admin", "email": "admin@example.com",
            "password": "Another!789", "role": "admin",
        })
        assert updated.status_code == 200
        # Their previous session token died with the password change.
        assert client.get("/api/admin/auth/me", headers=headers).status_code == 401
