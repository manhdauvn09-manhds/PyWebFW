"""User management: CRUD with business validation + audit trail."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import ConflictError, ValidationFailedError
from app.core.pagination import PageRequest, PageResult
from app.core.security import PasswordHasher
from app.core.validation import BaseValidator, ValidationResult
from app.domain.models import Role, User
from app.infrastructure.database.manager import BaseDatabaseManager
from app.infrastructure.database.unit_of_work import UnitOfWork
from app.repositories.log_repository import LogRepository
from app.repositories.user_repository import UserRepository
from app.services.base import AuditMixin, BaseService


@dataclass(frozen=True, slots=True)
class UserInput:
    username: str
    email: str
    password: str | None = None
    role: str = Role.VIEWER.value
    is_active: bool = True


class UserInputValidator(BaseValidator[UserInput]):
    def __init__(self, require_password: bool) -> None:
        self._require_password = require_password

    def _rules(self, subject: UserInput, result: ValidationResult) -> None:
        if self.require(result, "username", subject.username):
            self.min_length(result, "username", subject.username, 3)
        if self.require(result, "email", subject.email):
            self.valid_email(result, "email", subject.email)
        if self._require_password and self.require(result, "password", subject.password):
            self.min_length(result, "password", subject.password or "", 8)
        if subject.role not in {r.value for r in Role}:
            result.add("role", "is not a valid role")


class UserService(BaseService, AuditMixin):
    def __init__(
        self,
        db: BaseDatabaseManager,
        users: UserRepository,
        logs: LogRepository,
        hasher: PasswordHasher,
    ) -> None:
        super().__init__()
        self._db = db
        self._users = users
        self._audit_repo = logs
        self._hasher = hasher

    def list_users(self, page: PageRequest) -> PageResult[User]:
        return self._users.list_page(page)

    def get(self, user_id: int) -> User:
        return self._users.get_by_id(user_id)

    def create(self, data: UserInput, actor: str) -> User:
        UserInputValidator(require_password=True).validate(data).raise_if_invalid()
        if self._users.username_or_email_exists(data.username, data.email):
            raise ConflictError("Username or email already exists")
        user = User(
            username=data.username.strip(),
            email=data.email.strip().lower(),
            password_hash=self._hasher.hash(data.password or ""),
            role=Role(data.role),
            is_active=data.is_active,
        )
        with UnitOfWork(self._db):
            self._users.add(user)
            self._audit(actor, "user.created", target=user.username)
        return user

    def update(self, user_id: int, data: UserInput, actor: str) -> User:
        UserInputValidator(require_password=False).validate(data).raise_if_invalid()
        user = self._users.get_by_id(user_id)
        if self._users.username_or_email_exists(data.username, data.email, exclude_id=user_id):
            raise ConflictError("Username or email already exists")
        user.username = data.username.strip()
        user.email = data.email.strip().lower()
        user.role = Role(data.role)
        user.is_active = data.is_active
        if data.password:
            if len(data.password) < 8:
                raise ValidationFailedError("Password must be at least 8 characters")
            user.password_hash = self._hasher.hash(data.password)
            user.revoke_tokens()   # password change invalidates existing sessions
        with UnitOfWork(self._db):
            self._users.update(user)
            self._audit(actor, "user.updated", target=user.username)
        return user

    def revoke_sessions(self, user_id: int, actor: str) -> User:
        """Admin action: invalidates every outstanding token of the user."""
        user = self._users.get_by_id(user_id)
        user.revoke_tokens()
        with UnitOfWork(self._db):
            self._users.update(user)
            self._audit(actor, "user.sessions_revoked", target=user.username,
                        level="warning")
        return user

    def delete(self, user_id: int, actor: str) -> None:
        user = self._users.get_by_id(user_id)
        if user.username == actor:
            raise ValidationFailedError("You cannot delete your own account")
        with UnitOfWork(self._db):
            self._users.delete(user_id)
            self._audit(actor, "user.deleted", target=user.username, level="warning")
