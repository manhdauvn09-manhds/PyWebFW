"""Structured logging.

`BaseLogger` is the contract the rest of the framework depends on (DIP):
services/jobs never import `logging` directly, so the backend can be swapped
(JSON to stdout today, OTLP/file shipping tomorrow) without touching callers.
"""
from __future__ import annotations

import json
import logging
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any


class BaseLogger(ABC):
    """Contract for all framework loggers."""

    @abstractmethod
    def debug(self, message: str, **fields: Any) -> None: ...

    @abstractmethod
    def info(self, message: str, **fields: Any) -> None: ...

    @abstractmethod
    def warning(self, message: str, **fields: Any) -> None: ...

    @abstractmethod
    def error(self, message: str, **fields: Any) -> None: ...


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line — friendly to log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            entry.update(extra)
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class StructuredLogger(BaseLogger):
    """Adapter over stdlib logging emitting structured JSON lines."""

    def __init__(self, inner: logging.Logger) -> None:
        self._inner = inner

    def _log(self, level: int, message: str, fields: dict[str, Any]) -> None:
        self._inner.log(level, message, extra={"fields": fields})

    def debug(self, message: str, **fields: Any) -> None:
        self._log(logging.DEBUG, message, fields)

    def info(self, message: str, **fields: Any) -> None:
        self._log(logging.INFO, message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._log(logging.WARNING, message, fields)

    def error(self, message: str, **fields: Any) -> None:
        self._log(logging.ERROR, message, fields)

    def exception(self, message: str, **fields: Any) -> None:
        self._inner.error(message, exc_info=True, extra={"fields": fields})


class LoggerFactory:
    """Central place that configures handlers once and hands out loggers."""

    _configured = False

    @classmethod
    def configure(cls, *, debug: bool) -> None:
        if cls._configured:
            return
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLineFormatter())
        root = logging.getLogger("pywebfw")
        root.setLevel(logging.DEBUG if debug else logging.INFO)
        root.addHandler(handler)
        root.propagate = False
        cls._configured = True

    @classmethod
    def get(cls, name: str) -> StructuredLogger:
        return StructuredLogger(logging.getLogger(f"pywebfw.{name}"))
