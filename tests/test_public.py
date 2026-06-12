"""Public pages + public API."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_home_page_renders(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Welcome to TestApp" in response.text
    assert "<nav" in response.text and "<footer" in response.text


def test_content_pages_render(client: TestClient) -> None:
    for path in ("/about", "/contact", "/privacy-policy", "/terms",
                 "/editorial-policy", "/introduction", "/sitemap"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_search_page_escapes_query(client: TestClient) -> None:
    response = client.get("/search", params={"q": "<script>alert(1)</script>"})
    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text  # XSS-escaped


def test_rss_and_sitemap_xml(client: TestClient) -> None:
    rss = client.get("/rss")
    assert rss.status_code == 200 and rss.text.startswith("<?xml")
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200 and "urlset" in sitemap.text


def test_public_api_menus(client: TestClient) -> None:
    payload = client.get("/api/public/menus").json()
    assert payload["success"] is True
    assert any(item["title"] == "Home" for item in payload["data"])


def test_public_api_content_and_404(client: TestClient) -> None:
    ok = client.get("/api/public/content/about").json()
    assert ok["success"] and ok["data"]["slug"] == "about"
    missing = client.get("/api/public/content/nope")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "NOT_FOUND"


def test_public_api_search(client: TestClient) -> None:
    payload = client.get("/api/public/search", params={"q": "privacy"}).json()
    assert payload["success"] is True
    assert payload["meta"]["total"] >= 1


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
