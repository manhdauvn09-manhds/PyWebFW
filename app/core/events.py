"""In-process domain event bus (Observer pattern).

Services publish facts ("contact.submitted", "content.slug_changed") without
knowing who reacts; handlers are wired in bootstrap. Handlers are isolated —
one failing subscriber never breaks the publisher or other subscribers.
Swap for a message queue behind the same interface when going multi-process.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.logging import LoggerFactory
from app.domain.models import utc_now_iso


@dataclass(frozen=True, slots=True)
class DomainEvent:
    name: str
    payload: dict[str, Any]
    occurred_at: str = field(default_factory=utc_now_iso)


EventHandler = Callable[[DomainEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()
        self._logger = LoggerFactory.get("events")

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        with self._lock:
            self._handlers.setdefault(event_name, []).append(handler)

    def publish(self, event_name: str, payload: dict[str, Any]) -> DomainEvent:
        event = DomainEvent(name=event_name, payload=payload)
        with self._lock:
            handlers = list(self._handlers.get(event_name, ()))
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:   # subscriber isolation
                self._logger.error("event handler failed", event=event_name,
                                   handler=getattr(handler, "__name__", repr(handler)),
                                   error=str(exc))
        return event
