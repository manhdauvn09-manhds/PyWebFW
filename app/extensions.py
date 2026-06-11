"""Demo plugin — the reference for extending pywebfw from application code.

Adds one custom public page (/hello) and one custom scheduled job without
touching anything inside the pywebfw package.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from pywebfw.config.settings import AppSettings
from pywebfw.core.container import ServiceContainer
from pywebfw.core.routing import BaseController
from pywebfw.domain.models import MenuArea
from pywebfw.plugins import AppModule
from pywebfw.scheduler.base import BaseSchedulerJob, IntervalSchedule
from pywebfw.services.menu_service import MenuService
from pywebfw.web.components import esc
from pywebfw.web.pages.base import PageContext, PublicPage


class HelloPage(PublicPage):
    """A fully custom page — still gets the shared layout, menu, SEO, CSP."""

    @property
    def title(self) -> str:
        return "Hello"

    def build_content(self) -> str:
        return (f"<h1>Hello from a plugin!</h1>"
                f"<p>This page lives in the application package "
                f"(<code>app/extensions.py</code>), not in {esc('pywebfw')}.</p>")


class HelloWebController(BaseController):
    tags = ["demo"]

    def __init__(self, settings: AppSettings, menus: MenuService) -> None:
        self._settings = settings
        self._menus = menus

    def _register(self, router: APIRouter) -> None:
        @router.get("/hello", response_class=HTMLResponse, include_in_schema=False)
        def hello(request: Request) -> HTMLResponse:
            ctx = PageContext(
                site_name=self._settings.name,
                path=request.url.path,
                menu_items=self._menus.get_menu(MenuArea.PUBLIC),
                csp_nonce=getattr(request.state, "csp_nonce", ""),
            )
            return HTMLResponse(HelloPage(ctx).render())


class HeartbeatJob(BaseSchedulerJob):
    """Custom job example — appears in /admin/jobs like any built-in."""

    name = "demo-heartbeat"
    schedule = IntervalSchedule(300)

    def run(self) -> str:
        return "demo application heartbeat ok"


class DemoModule(AppModule):
    name = "demo"

    def controllers(self, container: ServiceContainer,
                    settings: AppSettings) -> list[BaseController]:
        return [HelloWebController(settings, container.resolve(MenuService))]

    def jobs(self, container: ServiceContainer) -> list[BaseSchedulerJob]:
        return [HeartbeatJob()]
