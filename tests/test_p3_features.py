"""P3: FTS5 search robustness + CSP nonce header."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_fts_search_finds_seeded_content(client: TestClient) -> None:
    payload = client.get("/api/public/search", params={"q": "privacy"}).json()
    assert payload["meta"]["total"] >= 1
    assert any(item["slug"] == "privacy-policy" for item in payload["data"])


def test_fts_prefix_match(client: TestClient) -> None:
    # 'maintain' should prefix-match 'maintainable' in the introduction body.
    payload = client.get("/api/public/search", params={"q": "maintain"}).json()
    assert payload["meta"]["total"] >= 1


def test_fts_operators_cannot_be_injected(client: TestClient) -> None:
    # Quotes / FTS operators in user input must not raise, just return safely.
    for query in ('pri"vacy', "NEAR(privacy)", "privacy OR", '"" AND ""'):
        response = client.get("/api/public/search", params={"q": query})
        assert response.status_code == 200, query
        assert response.json()["success"] is True


def test_csp_header_with_matching_nonce(client: TestClient) -> None:
    response = client.get("/")
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    nonce = csp.split("nonce-")[1].split("'")[0]
    # The inline <style> carries the SAME nonce the header allows.
    assert f'nonce="{nonce}"' in response.text


def test_csp_nonce_changes_per_request(client: TestClient) -> None:
    csp1 = client.get("/").headers["content-security-policy"]
    csp2 = client.get("/").headers["content-security-policy"]
    assert csp1 != csp2


def test_login_page_script_has_nonce(client: TestClient) -> None:
    response = client.get("/admin/login")
    csp = response.headers["content-security-policy"]
    nonce = csp.split("nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in response.text
