"""Domain-level validation framework.

Pydantic validates *shape* at the API boundary; `BaseValidator` subclasses
validate *business rules* in the service layer (uniqueness, policies, ...).
Template Method: `validate()` is fixed, subclasses implement `_rules()`.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pywebfw.core.exceptions import ValidationFailedError

T = TypeVar("T")

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    field: str
    message: str


@dataclass
class ValidationResult:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def add(self, field_name: str, message: str) -> None:
        self.issues.append(ValidationIssue(field_name, message))

    def raise_if_invalid(self) -> None:
        if not self.is_valid:
            raise ValidationFailedError(
                "Validation failed",
                details=[{"field": i.field, "message": i.message} for i in self.issues],
            )


class BaseValidator(ABC, Generic[T]):
    """Template Method base for business-rule validators."""

    def validate(self, subject: T) -> ValidationResult:
        result = ValidationResult()
        self._rules(subject, result)
        return result

    @abstractmethod
    def _rules(self, subject: T, result: ValidationResult) -> None: ...

    # --- reusable rule helpers -------------------------------------------
    @staticmethod
    def require(result: ValidationResult, field_name: str, value: Any) -> bool:
        if value is None or (isinstance(value, str) and not value.strip()):
            result.add(field_name, "is required")
            return False
        return True

    @staticmethod
    def min_length(result: ValidationResult, field_name: str, value: str, length: int) -> None:
        if value is not None and len(value) < length:
            result.add(field_name, f"must be at least {length} characters")

    @staticmethod
    def valid_email(result: ValidationResult, field_name: str, value: str) -> None:
        if value and not _EMAIL_RE.match(value):
            result.add(field_name, "is not a valid email address")
