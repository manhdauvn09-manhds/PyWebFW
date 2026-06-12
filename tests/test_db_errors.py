"""Database error mapping: constraint races -> 409, no schema leakage."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pywebfw.core.exceptions import ConflictError, DatabaseError
from pywebfw.domain.models import User
from pywebfw.infrastructure.database.manager import BaseDatabaseManager
from pywebfw.repositories.user_repository import UserRepository


def test_unique_violation_maps_to_conflict_not_500(client: TestClient) -> None:
    """Even when a duplicate slips past the service-level pre-check (race
    between two requests), the DB layer raises ConflictError (409), not a
    raw 500 — and the message contains no table/column names."""
    repo = client.app.state.container.resolve(UserRepository)
    with pytest.raises(ConflictError) as exc_info:
        repo.add(User(username="admin", email="admin@example.com", password_hash="x"))
    message = exc_info.value.message
    assert "users" not in message and "UNIQUE" not in message
    assert exc_info.value.status_code == 409


def test_database_errors_are_sanitized(client: TestClient) -> None:
    """Driver error details (schema names) never reach the client payload."""
    db = client.app.state.container.resolve(BaseDatabaseManager)
    with pytest.raises(DatabaseError) as exc_info:
        db.fetch_all("SELECT * FROM table_that_does_not_exist")
    assert exc_info.value.message == "Database query failed"
    assert "table_that_does_not_exist" not in exc_info.value.message
