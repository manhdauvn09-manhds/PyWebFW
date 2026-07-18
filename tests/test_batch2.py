"""Batch 2: contact form (honeypot + throttle), media manager,
session manager, backup manager."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
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


