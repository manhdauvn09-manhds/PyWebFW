from __future__ import annotations

from typing import Any

from app.domain.models import ContactMessage
from app.repositories.base import BaseRepository


class ContactRepository(BaseRepository[ContactMessage]):
    @property
    def table_name(self) -> str:
        return "contact_messages"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "name", "email", "is_read", "created_at"})

    def _map_row(self, row: dict[str, Any]) -> ContactMessage:
        return ContactMessage(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            subject=row["subject"],
            message=row["message"],
            ip_hash=row["ip_hash"],
            is_read=bool(row["is_read"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: ContactMessage) -> dict[str, Any]:
        return {
            "name": entity.name,
            "email": entity.email,
            "subject": entity.subject,
            "message": entity.message,
            "ip_hash": entity.ip_hash,
            "is_read": int(entity.is_read),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def count_unread(self) -> int:
        return self.count("is_read = 0")
