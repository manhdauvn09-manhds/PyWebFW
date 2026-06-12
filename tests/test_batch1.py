"""Batch 1: traffic analytics, online users, jobs monitor, system settings,
maintenance mode, robots.txt, styled error pages."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_robots_txt(client: TestClient) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /admin" in response.text
    assert "Sitemap:" in response.text


def test_unknown_page_gets_styled_404(client: TestClient) -> None:
    response = client.get("/no-such-page")
    assert response.status_code == 404
    assert "error-box" in response.text and "404" in response.text
    # The inline style carries the CSP nonce allowed by the header.
    nonce = response.headers["content-security-policy"].split("nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in response.text


def test_unknown_api_path_gets_json_envelope(client: TestClient) -> None:
    response = client.get("/api/no-such-endpoint")
    assert response.status_code == 404
    payload = response.json()
    assert payload["success"] is False and payload["error"]["code"] == "HTTP_ERROR"


def test_pydantic_validation_uses_standard_envelope(client: TestClient) -> None:
    response = client.post("/api/admin/auth/login", json={"username": "admin"})
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert any(item["field"] == "password" for item in payload["error"]["details"])


def test_settings_api_and_maintenance_mode(client: TestClient,
                                           auth_headers: dict[str, str]) -> None:
    current = client.get("/api/admin/settings", headers=auth_headers).json()["data"]
    assert current["maintenance_mode"] == "0"

    unknown = client.put("/api/admin/settings", headers=auth_headers,
                         json={"values": {"not_a_setting": "x"}})
    assert unknown.status_code == 422

    try:
        client.put("/api/admin/settings", headers=auth_headers,
                   json={"values": {"maintenance_mode": "1"}})
        public = client.get("/")
        assert public.status_code == 503
        assert "maintenance" in public.text.lower()
        assert public.headers["retry-after"] == "300"
        # Admin area and health probe stay reachable to switch it back off.
        assert client.get("/healthz").status_code == 200
        assert client.get("/admin/login").status_code == 200
    finally:
        client.put("/api/admin/settings", headers=auth_headers,
                   json={"values": {"maintenance_mode": "0"}})
    assert client.get("/").status_code == 200


def test_settings_admin_page(client: TestClient) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/settings")
    assert page.status_code == 200
    assert 'data-setting="maintenance_mode"' in page.text
    client.cookies.clear()


def test_jobs_monitor_page_and_run_now(client: TestClient,
                                       auth_headers: dict[str, str]) -> None:
    client.post("/api/admin/auth/login",
                json={"username": "admin", "password": "ChangeMe!123"})
    page = client.get("/admin/jobs")
    assert page.status_code == 200
    assert "database-health-check" in page.text and "Run now" in page.text
    client.cookies.clear()

    listed = client.get("/api/admin/system/jobs", headers=auth_headers).json()
    assert listed["data"]["available"] is True
    job_names = [entry["job"] for entry in listed["data"]["jobs"]]
    assert "database-health-check" in job_names

    run = client.post("/api/admin/system/jobs/database-health-check/run",
                      headers=auth_headers)
    assert run.status_code == 200
    assert run.json()["data"]["status"] == "success"

    missing = client.post("/api/admin/system/jobs/nope/run", headers=auth_headers)
    assert missing.status_code == 404
