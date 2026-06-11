"""Authentication flows: credential check -> signed token. Failed and
successful logins both leave an audit trail."""
from __future__ import annotations

from dataclasses import dataclass

from pywebfw.core.exceptions import AuthenticationError, ValidationFailedError
from pywebfw.core.security import PasswordHasher, TokenManager, TotpProvider
from pywebfw.domain.models import User
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.repositories.user_repository import UserRepository
from pywebfw.services.base import AuditMixin, BaseService


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: str
    user: User


class AuthService(BaseService, AuditMixin):
    def __init__(
        self,
        users: UserRepository,
        logs: LogRepository,
        hasher: PasswordHasher,
        tokens: TokenManager,
        totp: TotpProvider,
    ) -> None:
        super().__init__()
        self._users = users
        self._audit_repo = logs
        self._hasher = hasher
        self._tokens = tokens
        self._totp = totp

    def login(self, username: str, password: str, otp: str | None = None) -> LoginResult:
        user = self._users.find_by_username(username.strip())
        # Same error for unknown user / wrong password — no user enumeration.
        if user is None or not user.is_active or not self._hasher.verify(password, user.password_hash):
            self._audit("anonymous", "login.failed", target=username, level="warning")
            self._logger.warning("login failed", username=username)
            raise AuthenticationError("Invalid username or password")
        if user.totp_enabled:
            if not otp:
                # The password was right — tell the client to ask for the code.
                raise AuthenticationError("One-time code required",
                                          details={"reason": "otp_required"})
            if not self._totp.verify(user.totp_secret, otp):
                self._audit(user.username, "login.otp_failed", level="warning")
                raise AuthenticationError("Invalid one-time code")
        token = self._issue_for(user)
        self._audit(user.username, "login.success")
        return LoginResult(token=token, user=user)

    # --- two-factor authentication -----------------------------------------------
    def setup_totp(self, user_id: int) -> dict[str, str]:
        """Generates and stores a new secret (not yet enabled); the user
        confirms with a valid code via enable_totp."""
        user = self._users.get_by_id(user_id)
        secret = TotpProvider.generate_secret()
        user.totp_secret = secret
        user.totp_enabled = False
        self._users.update(user)
        self._audit(user.username, "auth.2fa_setup_started")
        return {"secret": secret,
                "otpauth_uri": self._totp.provisioning_uri(secret, user.username)}

    def enable_totp(self, user_id: int, otp: str) -> None:
        user = self._users.get_by_id(user_id)
        if not user.totp_secret or not self._totp.verify(user.totp_secret, otp):
            raise AuthenticationError("Invalid one-time code")
        user.totp_enabled = True
        self._users.update(user)
        self._audit(user.username, "auth.2fa_enabled")

    def disable_totp(self, user_id: int, otp: str) -> None:
        user = self._users.get_by_id(user_id)
        if not user.totp_enabled or not self._totp.verify(user.totp_secret, otp):
            raise AuthenticationError("Invalid one-time code")
        user.totp_secret = ""
        user.totp_enabled = False
        self._users.update(user)
        self._audit(user.username, "auth.2fa_disabled", level="warning")

    def change_password(self, user_id: int, current_password: str,
                        new_password: str) -> LoginResult:
        """Verifies the current password, stores the new hash, revokes every
        outstanding token, clears the must-change flag, returns a fresh token."""
        user = self._users.get_by_id(user_id)
        if not self._hasher.verify(current_password, user.password_hash):
            self._audit(user.username, "auth.password_change_failed", level="warning")
            raise AuthenticationError("Current password is incorrect")
        if len(new_password) < 8:
            raise ValidationFailedError("New password must be at least 8 characters")
        user.password_hash = self._hasher.hash(new_password)
        user.revoke_tokens()
        user.must_change_password = False
        self._users.update(user)
        self._audit(user.username, "auth.password_changed")
        return LoginResult(token=self._issue_for(user), user=user)

    def revoke_tokens(self, user_id: int) -> None:
        """Logout-everywhere: every previously issued token becomes invalid."""
        user = self._users.get_by_id(user_id)
        user.revoke_tokens()
        self._users.update(user)
        self._audit(user.username, "auth.logout")

    def get_user(self, user_id: int) -> User:
        return self._users.get_by_id(user_id)

    def _issue_for(self, user: User) -> str:
        return self._tokens.issue(subject=str(user.id), role=user.role.value,
                                  version=user.token_version)
