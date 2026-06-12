from __future__ import annotations

from typing import Any

from pywebfw.domain.models import Role, User
from pywebfw.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    @property
    def table_name(self) -> str:
        return "users"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "username", "email", "role", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> User:
        return User(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"],
            role=Role(row["role"]),
            is_active=bool(row["is_active"]),
            token_version=row["token_version"],
            must_change_password=bool(row["must_change_password"]),
            totp_secret=row["totp_secret"],
            totp_enabled=bool(row["totp_enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: User) -> dict[str, Any]:
        return {
            "username": entity.username,
            "email": entity.email,
            "password_hash": entity.password_hash,
            "role": entity.role.value,
            "is_active": int(entity.is_active),
            "token_version": entity.token_version,
            "must_change_password": int(entity.must_change_password),
            "totp_secret": entity.totp_secret,
            "totp_enabled": int(entity.totp_enabled),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def find_by_username(self, username: str) -> User | None:
        row = self._db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
        return self._map_row(row) if row else None

    def username_or_email_exists(self, username: str, email: str, exclude_id: int | None = None) -> bool:
        sql = "SELECT COUNT(*) AS n FROM users WHERE (username = ? OR email = ?)"
        params: list[Any] = [username, email]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        row = self._db.fetch_one(sql, params)
        return bool(row and row["n"] > 0)
