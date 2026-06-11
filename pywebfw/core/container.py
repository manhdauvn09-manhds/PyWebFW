"""Minimal type-keyed Dependency Injection container.

Services declare their dependencies through constructors; only the bootstrap
layer knows concrete wiring. Supports singletons and per-resolve factories.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

from pywebfw.core.exceptions import ConfigurationError

T = TypeVar("T")


class ServiceContainer:
    def __init__(self) -> None:
        self._singletons: dict[type, Any] = {}
        self._factories: dict[type, Callable[["ServiceContainer"], Any]] = {}
        self._singleton_factories: dict[type, Callable[["ServiceContainer"], Any]] = {}
        self._lock = threading.RLock()

    def register_instance(self, key: type[T], instance: T) -> None:
        with self._lock:
            self._singletons[key] = instance

    def register_singleton(self, key: type[T], factory: Callable[["ServiceContainer"], T]) -> None:
        """Lazily constructed, cached after first resolve."""
        with self._lock:
            self._singleton_factories[key] = factory

    def register_factory(self, key: type[T], factory: Callable[["ServiceContainer"], T]) -> None:
        """A fresh instance per resolve (transient scope)."""
        with self._lock:
            self._factories[key] = factory

    def resolve(self, key: type[T]) -> T:
        with self._lock:
            if key in self._singletons:
                return self._singletons[key]
            if key in self._singleton_factories:
                instance = self._singleton_factories.pop(key)(self)
                self._singletons[key] = instance
                return instance
            if key in self._factories:
                return self._factories[key](self)
        raise ConfigurationError(f"No registration for {key.__name__}")

    def has(self, key: type) -> bool:
        with self._lock:
            return key in self._singletons or key in self._singleton_factories or key in self._factories
