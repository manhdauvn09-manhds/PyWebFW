"""Authentication / authorization.

- `BaseAuthHandler` (ABC): contract — turn an HTTP request into a principal.
- `TokenAuthHandler`: bearer header OR HttpOnly cookie (admin pages). The
  token's embedded version is checked against `users.token_version`, so
  logout / password change revokes every outstanding token immediately.
- `AuthGuard` / `RoleGuard`: callable guard objects used as FastAPI
  dependencies — RoleGuard *is an* AuthGuard with RBAC plus an optional
  "must change password first" policy gate.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from fastapi import Request

from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.security import TokenManager
from app.repositories.user_repository import UserRepository

ADMIN_TOKEN_COOKIE = "admin_token"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Authenticated principal attached to a request."""

    id: int
    role: str
    must_change_password: bool = False

    def has_role(self, *roles: str) -> bool:
        return self.role in roles


class BaseAuthHandler(ABC):
    @abstractmethod
    def authenticate_request(self, request: Request) -> CurrentUser: ...


class TokenAuthHandler(BaseAuthHandler):
    def __init__(self, tokens: TokenManager, users: UserRepository) -> None:
        self._tokens = tokens
        self._users = users

    def authenticate_request(self, request: Request) -> CurrentUser:
        token = self._extract_token(request)
        if not token:
            raise AuthenticationError("Missing credentials")
        payload = self._tokens.verify(token)
        user = self._users.find_by_id(int(payload.subject))
        if user is None or not user.is_active:
            raise AuthenticationError("Account disabled")
        if payload.version != user.token_version:
            raise AuthenticationError("Token revoked")
        # Role comes from the DB, not the token: role changes apply instantly.
        return CurrentUser(id=user.id, role=user.role.value,
                           must_change_password=user.must_change_password)

    @staticmethod
    def _extract_token(request: Request) -> str | None:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header.removeprefix("Bearer ").strip()
        return request.cookies.get(ADMIN_TOKEN_COOKIE)


class AuthGuard:
    """FastAPI dependency: requires a valid principal."""

    def __init__(self, handler: BaseAuthHandler) -> None:
        self._handler = handler

    def __call__(self, request: Request) -> CurrentUser:
        return self._handler.authenticate_request(request)


class RoleGuard(AuthGuard):
    """AuthGuard + role-based access control + password-change policy."""

    def __init__(self, handler: BaseAuthHandler, *roles: str,
                 enforce_password_change: bool = False) -> None:
        super().__init__(handler)
        self._roles = roles
        self._enforce_password_change = enforce_password_change

    def __call__(self, request: Request) -> CurrentUser:
        user = super().__call__(request)
        if not user.has_role(*self._roles):
            raise AuthorizationError("Insufficient permissions")
        if self._enforce_password_change and user.must_change_password:
            raise AuthorizationError(
                "Password change required before continuing",
                details={"reason": "password_change_required"},
            )
        return user
