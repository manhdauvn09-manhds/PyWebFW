"""Public contact form handling with layered anti-spam:
honeypot field (silent drop), per-sender rate limit, boundary validation.
A new message best-effort notifies the configured admin email.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import RateLimitExceededError
from app.core.pagination import PageRequest, PageResult
from app.core.security import SlidingWindowRateLimiter
from app.domain.models import ContactMessage
from app.infrastructure.mail.mailer import BaseMailer
from app.repositories.contact_repository import ContactRepository
from app.repositories.log_repository import LogRepository
from app.services.base import AuditMixin, BaseService


@dataclass(frozen=True, slots=True)
class ContactInput:
    name: str
    email: str
    subject: str
    message: str
    honeypot: str = ""        # the hidden "website" field — humans leave it empty


class ContactService(BaseService, AuditMixin):
    def __init__(self, messages: ContactRepository, logs: LogRepository,
                 mailer: BaseMailer, admin_email: str = "",
                 max_per_window: int = 3, window_seconds: int = 600) -> None:
        super().__init__()
        self._messages = messages
        self._audit_repo = logs
        self._mailer = mailer
        self._admin_email = admin_email
        self._limiter = SlidingWindowRateLimiter(max_per_window, window_seconds)

    def submit(self, data: ContactInput, ip_hash: str) -> ContactMessage | None:
        """Returns the stored message, or None when the honeypot tripped
        (the caller still answers success — never tip off the bot)."""
        if data.honeypot.strip():
            self._logger.warning("contact honeypot tripped", ip_hash=ip_hash)
            return None
        if not self._limiter.allow(ip_hash):
            raise RateLimitExceededError("Too many messages, please try again later")
        entry = ContactMessage(
            name=data.name.strip(),
            email=data.email.strip().lower(),
            subject=data.subject.strip(),
            message=data.message.strip(),
            ip_hash=ip_hash,
        )
        self._messages.add(entry)
        self._audit("anonymous", "contact.submitted", target=entry.email)
        self._notify_admin(entry)
        return entry

    def _notify_admin(self, entry: ContactMessage) -> None:
        if not self._admin_email:
            return
        # Best-effort: a mail failure must never fail the submission.
        self._mailer.send(
            self._admin_email,
            f"[Contact] {entry.subject or 'New message'} — {entry.name}",
            f"From: {entry.name} <{entry.email}>\n\n{entry.message}",
        )

    # --- admin management -------------------------------------------------------
    def list_messages(self, page: PageRequest,
                      unread_only: bool = False) -> PageResult[ContactMessage]:
        where = "is_read = 0" if unread_only else None
        return self._messages.list_page(page, where)

    def unread_count(self) -> int:
        return self._messages.count_unread()

    def mark_read(self, message_id: int, actor: str) -> ContactMessage:
        entry = self._messages.get_by_id(message_id)
        entry.is_read = True
        self._messages.update(entry)
        self._audit(actor, "contact.read", target=entry.email)
        return entry

    def delete(self, message_id: int, actor: str) -> None:
        entry = self._messages.get_by_id(message_id)
        self._messages.delete(message_id)
        self._audit(actor, "contact.deleted", target=entry.email, level="warning")
