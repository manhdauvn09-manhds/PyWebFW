"""Content Management (admin CMS) + database backup job."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from pywebfw.scheduler.base import JobStatus


def test_content_crud_flow_end_to_end(client: TestClient, auth_headers: dict[str, str]) -> None:
    # Create — immediately visible on the public side.
    created = client.post("/api/admin/contents", headers=auth_headers, json={
        "slug": "news-flash", "title": "News Flash",
        "summary": "A zebra escaped", "body": "Full zebra story here.",
    })
    assert created.status_code == 201, created.text
    content_id = created.json()["data"]["id"]
    assert client.get("/api/public/content/news-flash").status_code == 200

    # FTS triggers index new rows: search finds the fresh content.
    found = client.get("/api/public/search", params={"q": "zebra"}).json()
    assert any(item["slug"] == "news-flash" for item in found["data"])

    # Duplicate slug -> conflict; invalid slug -> shape validation.
    assert client.post("/api/admin/contents", headers=auth_headers, json={
        "slug": "news-flash", "title": "Dup"}).status_code == 409
    assert client.post("/api/admin/contents", headers=auth_headers, json={
        "slug": "Bad Slug!", "title": "Nope"}).status_code == 422

    # Update with a new slug: cache invalidated, old slug gone, new one live.
    updated = client.put(f"/api/admin/contents/{content_id}", headers=auth_headers, json={
        "slug": "breaking-news", "title": "Breaking News",
        "summary": "A zebra escaped", "body": "Full zebra story here.",
    })
    assert updated.status_code == 200
    assert client.get("/api/public/content/news-flash").status_code == 404
    assert client.get("/api/public/content/breaking-news").status_code == 200

    # Unpublish hides it from the public site without deleting.
    client.put(f"/api/admin/contents/{content_id}", headers=auth_headers, json={
        "slug": "breaking-news", "title": "Breaking News", "is_published": False,
    })
    assert client.get("/api/public/content/breaking-news").status_code == 404

    # Delete; audit trail recorded the lifecycle.
    assert client.delete(f"/api/admin/contents/{content_id}",
                         headers=auth_headers).status_code == 200
    logs = client.get("/api/admin/logs", headers=auth_headers,
                      params={"size": 50}).json()
    actions = {entry["action"] for entry in logs["data"]}
    assert {"content.created", "content.updated", "content.deleted"} <= actions


def test_content_admin_page_renders_list_and_form(client: TestClient) -> None:
    login = client.post("/api/admin/auth/login",
                        json={"username": "admin", "password": "ChangeMe!123"})
    assert login.status_code == 200
    listing = client.get("/admin/contents")
    assert listing.status_code == 200
    assert "About Us" in listing.text                # seeded content listed
    new_form = client.get("/admin/contents", params={"new": "1"})
    assert 'id="content-form"' in new_form.text
    edit_form = client.get("/admin/contents", params={"edit": "1"})
    assert 'data-content-id="1"' in edit_form.text   # populated edit form
    client.cookies.clear()


def test_database_backup_job(client: TestClient) -> None:
    engine = client.app.state.scheduler_engine
    result = asyncio.run(engine.run_job_now("database-backup"))
    assert result.status is JobStatus.SUCCESS, result.error
    assert "backup" in result.message and "created" in result.message
    # Idempotent: a second run creates another timestamped snapshot.
    again = asyncio.run(engine.run_job_now("database-backup"))
    assert again.status is JobStatus.SUCCESS
