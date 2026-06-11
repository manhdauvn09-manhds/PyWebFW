"""Plugin contract: how applications extend the framework without forking it.

An `AppModule` bundles everything one feature needs — services, controllers
(pages/APIs), scheduled jobs, event subscriptions, schema. The application
passes its modules to `ApplicationBuilder(plugins=[...])`; the builder calls
each hook at the right phase of startup. Every hook is optional.

Example:

    class BlogModule(AppModule):
        name = "blog"

        def register_services(self, container, settings):
            container.register_singleton(BlogService, lambda c: BlogService(
                c.resolve(BaseDatabaseManager)))

        def controllers(self, container, settings):
            return [BlogWebController(settings, container.resolve(BlogService))]

        def jobs(self, container):
            return [BlogDigestJob(container.resolve(BlogService))]
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pywebfw.config.settings import AppSettings
    from pywebfw.core.container import ServiceContainer
    from pywebfw.core.events import EventBus
    from pywebfw.core.routing import BaseController
    from pywebfw.infrastructure.database.manager import BaseDatabaseManager
    from pywebfw.scheduler.base import BaseSchedulerJob


class AppModule:
    """Base class for application plugins. Override only what you need."""

    name: str = "module"

    def register_services(self, container: "ServiceContainer",
                          settings: "AppSettings") -> None:
        """Register the module's services into the DI container."""

    def init_schema(self, db: "BaseDatabaseManager") -> None:
        """Create/migrate the module's tables (must be idempotent)."""

    def controllers(self, container: "ServiceContainer",
                    settings: "AppSettings") -> list["BaseController"]:
        """Web/API controllers to mount (before the public /{slug} catch-all)."""
        return []

    def jobs(self, container: "ServiceContainer") -> list["BaseSchedulerJob"]:
        """Scheduled jobs to register (only runs in scheduler-enabled processes)."""
        return []

    def subscribe_events(self, bus: "EventBus",
                         container: "ServiceContainer") -> None:
        """Attach domain-event handlers."""
