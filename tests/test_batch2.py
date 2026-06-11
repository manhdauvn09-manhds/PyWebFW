"""Batch 2: contact form (honeypot + throttle), media manager,
session manager, backup manager."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.bootstrap import ApplicationBuilder
from tests.conftest import build_test_settings, unlock_seed_admin

PNG_BYTES = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


def _valid_contact(message: str = "Hello, I would like to know more.") -> dict:
    return {"name": "Nguyen Van A", "email": "a@example.com",
            "subject": "Question", "message": message}


# --- contact form --------------------------------------------------------------
def test_contact_submission_reaches_admin_inbox(client: TestClient,
                                                auth_headers: dict[str, str]) -> None:
    response = client.post("/api/public/contact", json=_valid_contact())
    assert response.status_code == 201
    assert response.json()["data"]["received"] is True

    inbox = client.get("/api/admin/messages", headers=auth_headers).json()
    assert inbox["meta"]["total"] >= 1
    entry = inbox["data"][0]
    assert entry["email"] == "a@example.com"
    assert entry["is_read"] is False

    # mark read + delete round-trip
    message_id = entry["id"]
    read = client.post(f"/api/admin/messages/{message_id}/read", headers=auth_headers)
    assert read.json()["data"]["is_read"] is True
    assert client.delete(f"/api/admin/messages/{message_id}",
                         headers=auth_headers).status_code == 200


def test_contact_honeypot_silently_drops(client: TestClient,
                                         auth_headers: dict[str, str]) -> None:
    before = client.get("/api/admin/messages",
                        headers=auth_headers).json()["meta"]["total"]
    bot = _valid_contact() | {"website": "http://spam.example.com"}
    response = client.post("/api/public/contact", json=bot)
    assert response.status_code == 201          # bot sees success...
    after = client.get("/api/admin/messages",
                       headers=auth_headers).json()["meta"]["total"]
    assert after == before                       # ...but nothing was stored


def test_contact_validation_and_throttle(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "contact.db"))
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app) as client:
        bad = client.post("/api/public/contact",
                          json=_valid_contact() | {"email": "not-an-email"})
        assert bad.status_code == 422

        for i in range(3):
            ok = client.post("/api/public/contact",
                             json=_valid_contact(f"Message number {i} with padding."))
            assert ok.status_code == 201
        blocked = client.post("/api/public/contact", json=_valid_contact())
        assert blocked.status_code == 429


def test_contact_page_renders_form(client: TestClient) -> None:
    page = client.get("/contact")
    assert page.status_code == 200
    assert 'id="contact-form"' in page.text
    assert 'name="website"' in page.text        # honeypot present but hidden
    assert "hp-field" in page.text


# --- media manager --------------------------------------------------------------
def test_media_upload_serve_delete_cycle(client: TestClient,
                                         auth_headers: dict[str, str]) -> None:
    uploaded = client.post("/api/admin/media", headers=auth_headers,
                           files={"file": ("photo.png", PNG_BYTES, "image/png")})
    assert uploaded.status_code == 201, uploaded.text
    name = uploaded.json()["data"]["name"]
    assert name.endswith(".png") and "photo" not in name   # randomized stored name

    listed = client.get("/api/admin/media", headers=auth_headers).json()
    assert any(f["name"] == name for f in listed["data"])

    served = client.get(f"/media/{name}")
    assert served.status_code == 200
    assert served.content == PNG_BYTES
    assert "max-age" in served.headers["cache-control"]

    assert client.delete(f"/api/admin/media/{name}",
                         headers=auth_headers).status_code == 200
    assert client.get(f"/media/{name}").status_code == 404


def test_media_rejects_bad_uploads(client: TestClient,
                                   auth_headers: dict[str, str]) -> None:
    exe = client.post("/api/admin/media", headers=auth_headers,
                      files={"file": ("virus.exe", b"MZ...", "application/octet-stream")})
    assert exe.status_code == 422

    too_big = client.post("/api/admin/media", headers=auth_headers,
                          files={"file": ("big.png", b"x" * (2 * 1024 * 1024), "image/png")})
    assert too_big.status_code == 422            # over the 1MB test cap

    traversal = client.get("/media/..%2F..%2Fapp.db")
    assert traversal.status_code in (404, 422)   # strict name pattern blocks it


def test_media_admin_page(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/media")
    assert page.status_code == 200 and 'id="upload-form"' in page.text
    client.cookies.clear()


# --- session manager --------------------------------------------------------------
def test_session_manager_page_and_revoke(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "sessions.db"))
    app = ApplicationBuilder(settings).build_app()
    with TestClient(app, follow_redirects=False) as client:
        unlock_seed_admin(client)
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        data = login.json()["data"]
        token, user_id = data["token"], data["user"]["id"]
        headers = {"Authorization": f"Bearer {token}"}

        page = client.get("/admin/sessions")
        assert page.status_code == 200
        assert "Revoke sessions" in page.text and "admin" in page.text

        revoked = client.post(f"/api/admin/users/{user_id}/revoke-sessions",
                              headers=headers)
        assert revoked.status_code == 200
        # The token that performed the revoke is itself dead now.
        assert client.get("/api/admin/auth/me", headers=headers).status_code == 401


# --- backup manager --------------------------------------------------------------
def test_backup_create_download_delete_cycle(client: TestClient,
                                             auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/backups", headers=auth_headers)
    assert created.status_code == 201, created.text
    name = created.json()["data"]["name"]

    listed = client.get("/api/admin/backups", headers=auth_headers).json()
    assert listed["data"]["supported"] is True
    assert any(b["name"] == name for b in listed["data"]["backups"])

    download = client.get(f"/api/admin/backups/{name}/download", headers=auth_headers)
    assert download.status_code == 200
    assert download.content.startswith(b"SQLite format 3")   # real DB snapshot

    bad_name = client.delete("/api/admin/backups/..%2Fapp.db", headers=auth_headers)
    assert bad_name.status_code in (404, 422)

    assert client.delete(f"/api/admin/backups/{name}",
                         headers=auth_headers).status_code == 200


def test_backup_admin_page(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/backups")
    assert page.status_code == 200 and "Create backup now" in page.text
    client.cookies.clear()
