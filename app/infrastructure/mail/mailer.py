"""Outbound email.

`BaseMailer` is the contract services depend on. `SmtpMailer` is the real
implementation; `NullMailer` (default when MAIL_HOST is unset) just logs, so
development and tests work without an SMTP server.

Sending is always best-effort from the caller's perspective: `send()` returns
False instead of raising, because a notification failure must never fail the
business operation that triggered it.
"""
from __future__ import annotations

import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from app.config.settings import MailSettings
from app.core.logging import LoggerFactory


class BaseMailer(ABC):
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> bool:
        """Returns True when the message was handed to the transport."""


class NullMailer(BaseMailer):
    """No-op transport: logs the message instead of sending it."""

    def __init__(self) -> None:
        self._logger = LoggerFactory.get("mail.null")

    def send(self, to: str, subject: str, body: str) -> bool:
        self._logger.info("mail suppressed (no MAIL_HOST configured)",
                          to=to, subject=subject)
        return True


class SmtpMailer(BaseMailer):
    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings
        self._logger = LoggerFactory.get("mail.smtp")

    def send(self, to: str, subject: str, body: str) -> bool:
        message = EmailMessage()
        message["From"] = self._settings.from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self._settings.host, self._settings.port,
                              timeout=10) as smtp:
                if self._settings.use_tls:
                    smtp.starttls()
                if self._settings.username:
                    smtp.login(self._settings.username, self._settings.password)
                smtp.send_message(message)
            self._logger.info("mail sent", to=to, subject=subject)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            self._logger.error("mail send failed", to=to, error=str(exc))
            return False
