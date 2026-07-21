"""Batch 3: redirects (manual + auto via event bus), dynamic content routes,
CSV export (with formula-injection protection), 2FA TOTP."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
from pywebfw.core.security import TotpProvider
from tests.conftest import build_test_settings, unlock_seed_admin


# --- redirects -------------------------------------------------------------------
def test_manual_redirect_rule(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/redirects", headers=auth_headers,
                          json={"from_path": "/old-page", "to_path": "/about"})
    assert created.status_code == 201, created.text

    response = client.get("/old-page")
    assert response.status_code == 301
    assert response.headers["location"] == "/about"

    # Hit counter incremented; duplicates and self-loops rejected.
    listed = client.get("/api/admin/redirects", headers=auth_headers).json()
    rule = next(r for r in listed["data"] if r["from_path"] == "/old-page")
    assert rule["hits"] >= 1
    assert client.post("/api/admin/redirects", headers=auth_headers,
                       json={"from_path": "/old-page", "to_path": "/x"}).status_code == 409
    assert client.post("/api/admin/redirects", headers=auth_headers,
                       json={"from_path": "/loop", "to_path": "/loop"}).status_code == 422

    assert client.delete(f"/api/admin/redirects/{rule['id']}",
                         headers=auth_headers).status_code == 200
    assert client.get("/old-page").status_code == 404


def test_slug_rename_creates_redirect_via_event_bus(client: TestClient,
                                                    auth_headers: dict[str, str]) -> None:
    created = client.post("/api/admin/contents", headers=auth_headers, json={
        "slug": "zebra-news", "title": "Zebra News",
        "summary": "stripes", "body": "All about zebras.",
    })
    content_id = created.json()["data"]["id"]

    # Dynamic catch-all route: admin-created content is reachable immediately.
    assert client.get("/zebra-news").status_code == 200
    assert "Zebra News" in client.get("/zebra-news").text

    client.put(f"/api/admin/contents/{content_id}", headers=auth_headers, json={
        "slug": "zebra-daily", "title": "Zebra News",
        "summary": "stripes", "body": "All about zebras.",
    })
    # The content.slug_changed event produced a 301 from old to new.
    moved = client.get("/zebra-news")
    assert moved.status_code == 301
    assert moved.headers["location"] == "/zebra-daily"
    assert client.get("/zebra-daily").status_code == 200

    client.delete(f"/api/admin/contents/{content_id}", headers=auth_headers)


def test_redirect_admin_page(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/redirects")
    assert page.status_code == 200 and 'id="redirect-form"' in page.text
    client.cookies.clear()


# --- CSV export -------------------------------------------------------------------
def test_csv_exports(client: TestClient, auth_headers: dict[str, str]) -> None:
    users = client.get("/api/admin/users/export", headers=auth_headers)
    assert users.status_code == 200
    assert users.headers["content-type"].startswith("text/csv")
    assert "users.csv" in users.headers["content-disposition"]
    assert "admin" in users.text and "password_hash" not in users.text

    logs = client.get("/api/admin/logs/export", headers=auth_headers)
    assert logs.status_code == 200 and "login.success" in logs.text
    # Export itself is audited.
    assert "users.exported" in logs.text


def test_csv_formula_injection_is_neutralized(client: TestClient,
                                              auth_headers: dict[str, str]) -> None:
    client.post("/api/public/contact", json={
        "name": "=HYPERLINK(evil)", "email": "x@example.com",
        "subject": "+cmd", "message": "A long enough message body here.",
    })
    export = client.get("/api/admin/messages/export", headers=auth_headers)
    assert "'=HYPERLINK(evil)" in export.text     # apostrophe-prefixed
    assert "'+cmd" in export.text


# --- 2FA TOTP --------------------------------------------------------------------
def test_totp_provider_roundtrip() -> None:
    provider = TotpProvider()
    secret = provider.generate_secret()
    code = provider.current_code(secret)
    assert provider.verify(secret, code)
    assert not provider.verify(secret, "000000") or code == "000000"
    assert not provider.verify("", code)
    assert "otpauth://totp/" in provider.provisioning_uri(secret, "admin")


def test_2fa_full_lifecycle(tmp_path) -> None:
    settings = build_test_settings(str(tmp_path / "tfa.db"))
    app = ApplicationBuilder(settings).build_app()
    provider = TotpProvider()
    with TestClient(app, follow_redirects=False) as client:
        unlock_seed_admin(client)
        login = client.post("/api/admin/auth/login",
                            json={"username": "admin", "password": "ChangeMe!123"})
        headers = {"Authorization": f"Bearer {login.json()['data']['token']}"}

        # setup -> confirm with a real code -> enabled
        setup = client.post("/api/admin/auth/2fa/setup", headers=headers).json()
        secret = setup["data"]["secret"]
        assert "otpauth://" in setup["data"]["otpauth_uri"]
        wrong = client.post("/api/admin/auth/2fa/enable", headers=headers,
                            json={"otp": "000000"})
        assert wrong.status_code == 401
        enabled = client.post("/api/admin/auth/2fa/enable", headers=headers,
                              json={"otp": provider.current_code(secret)})
        assert enabled.status_code == 200

        client.cookies.clear()
        # Password alone is no longer enough...
        no_otp = client.post("/api/admin/auth/login",
                             json={"username": "admin", "password": "ChangeMe!123"})
        assert no_otp.status_code == 401
        assert no_otp.json()["error"]["details"]["reason"] == "otp_required"
        bad_otp = client.post("/api/admin/auth/login",
                              json={"username": "admin", "password": "ChangeMe!123",
                                    "otp": "000000"})
        assert bad_otp.status_code == 401
        # ...the authenticator code completes the login.
        ok = client.post("/api/admin/auth/login",
                         json={"username": "admin", "password": "ChangeMe!123",
                               "otp": provider.current_code(secret)})
        assert ok.status_code == 200
        assert ok.json()["data"]["user"]["totp_enabled"] is True
        assert "totp_secret" not in ok.json()["data"]["user"]

        # disable -> password-only login works again
        headers2 = {"Authorization": f"Bearer {ok.json()['data']['token']}"}
        disabled = client.post("/api/admin/auth/2fa/disable", headers=headers2,
                               json={"otp": provider.current_code(secret)})
        assert disabled.status_code == 200
        client.cookies.clear()
        assert client.post("/api/admin/auth/login",
                           json={"username": "admin",
                                 "password": "ChangeMe!123"}).status_code == 200


def test_account_security_page_shows_2fa_section(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/change-password")
    assert page.status_code == 200
    assert "Two-factor authentication" in page.text
    assert 'id="tfa-setup"' in page.text
    client.cookies.clear()
