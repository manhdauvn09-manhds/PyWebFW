"""Service layer base.

`BaseService` gives every service a structured logger.
`AuditMixin` adds write-to-DB audit trail (composition of LogRepository) —
services that mutate data mix it in; read-only services don't carry the weight.
"""
from __future__ import annotations

from app.core.logging import BaseLogger, LoggerFactory
from app.domain.models import AuditLog
from app.repositories.log_repository import LogRepository


class BaseService:
    def __init__(self) -> None:
        self._logger: BaseLogger = LoggerFactory.get(self.__class__.__name__)


class AuditMixin:
    """Requires the host class to set `self._audit_repo: LogRepository`."""

    _audit_repo: LogRepository

    def _audit(self, actor: str, action: str, target: str = "", detail: str = "",
               level: str = "info") -> None:
        self._audit_repo.add(
            AuditLog(actor=actor, action=action, target=target, detail=detail, level=level))
