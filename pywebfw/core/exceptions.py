"""Error hierarchy. Every framework error maps to an HTTP status and a stable
machine-readable code, so the API layer can sanitize errors uniformly
(no stack traces or internals ever leak to clients).
"""
from __future__ import annotations

from typing import Any


class FrameworkError(Exception):
    """Root of the framework exception hierarchy."""

    status_code: int = 500
    error_code: str = "INTERNAL_ERROR"

    def __init__(self, message: str = "Internal error", *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def safe_payload(self) -> dict[str, Any]:
        """Client-safe representation (never includes internals)."""
        payload: dict[str, Any] = {"code": self.error_code, "message": self.message}
        if self.details is not None:
            payload["details"] = self.details
        return payload


class ConfigurationError(FrameworkError):
    status_code = 500
    error_code = "CONFIGURATION_ERROR"


class DatabaseError(FrameworkError):
    status_code = 500
    error_code = "DATABASE_ERROR"


class CacheError(FrameworkError):
    status_code = 500
    error_code = "CACHE_ERROR"


class ValidationFailedError(FrameworkError):
    status_code = 422
    error_code = "VALIDATION_FAILED"


class AuthenticationError(FrameworkError):
    status_code = 401
    error_code = "AUTHENTICATION_FAILED"


class AuthorizationError(FrameworkError):
    status_code = 403
    error_code = "FORBIDDEN"


class NotFoundError(FrameworkError):
    status_code = 404
    error_code = "NOT_FOUND"


class ConflictError(FrameworkError):
    status_code = 409
    error_code = "CONFLICT"


class RateLimitExceededError(FrameworkError):
    status_code = 429
    error_code = "RATE_LIMITED"


class SchedulerError(FrameworkError):
    status_code = 500
    error_code = "SCHEDULER_ERROR"
