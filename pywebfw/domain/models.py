"""Domain entities — plain dataclasses, persistence-agnostic.

`BaseEntity` carries identity + timestamps; concrete entities add their own
fields and any entity-specific behaviour (e.g. `User.to_public_dict`).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Role(str, Enum):
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class MenuArea(str, Enum):
    PUBLIC = "public"
    ADMIN = "admin"


@dataclass(slots=True)
class BaseEntity:
    id: int | None = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class User(BaseEntity):
    username: str = ""
    email: str = ""
    password_hash: str = ""
    role: Role = Role.VIEWER
    is_active: bool = True
    token_version: int = 0            # bumped on logout/password change -> revokes tokens
    must_change_password: bool = False
    totp_secret: str = ""
    totp_enabled: bool = False

    def revoke_tokens(self) -> None:
        self.token_version += 1

    def to_public_dict(self) -> dict[str, Any]:
        """Serialization that never exposes the password hash or internals."""
        data = self.to_dict()
        data.pop("password_hash", None)
        data.pop("token_version", None)
        data.pop("totp_secret", None)
        data["role"] = self.role.value
        return data

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


@dataclass(slots=True)
class MenuItem(BaseEntity):
    title: str = ""
    url: str = "/"
    area: MenuArea = MenuArea.PUBLIC
    parent_id: int | None = None
    position: int = 0
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["area"] = self.area.value
        return data


@dataclass(slots=True)
class AuditLog(BaseEntity):
    actor: str = "system"
    action: str = ""
    target: str = ""
    detail: str = ""
    level: str = "info"


@dataclass(slots=True)
class ContentItem(BaseEntity):
    slug: str = ""
    title: str = ""
    summary: str = ""
    body: str = ""
    seo_title: str = ""
    seo_description: str = ""
    is_published: bool = True


@dataclass(slots=True)
class Redirect(BaseEntity):
    """A 301/302 redirect rule, e.g. created automatically on slug changes."""

    from_path: str = ""
    to_path: str = ""
    status_code: int = 301
    hits: int = 0
    is_active: bool = True


@dataclass(slots=True)
class ContactMessage(BaseEntity):
    """A message submitted through the public contact form."""

    name: str = ""
    email: str = ""
    subject: str = ""
    message: str = ""
    ip_hash: str = ""              # anonymous sender hash, never the raw IP
    is_read: bool = False


@dataclass(slots=True)
class SettingEntry(BaseEntity):
    """A single key-value system setting, editable from the admin area."""

    key: str = ""
    value: str = ""


@dataclass(slots=True)
class DbConnectionProfile(BaseEntity):
    name: str = ""
    driver: str = "sqlite"
    dsn: str = ""
    pool_size: int = 5
    idle_timeout_seconds: int = 300
    is_default: bool = False

    def to_safe_dict(self) -> dict[str, Any]:
        """Masks credentials embedded in the DSN before serialization."""
        data = self.to_dict()
        if "@" in self.dsn and "://" in self.dsn:
            scheme, _, rest = self.dsn.partition("://")
            _, _, host_part = rest.rpartition("@")
            data["dsn"] = f"{scheme}://***:***@{host_part}"
        return data
