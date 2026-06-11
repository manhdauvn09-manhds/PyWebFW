"""Web (HTML) controllers — bind URLs to Page objects.

Adding a public screen: subclass PublicPage, add one entry to `_page_routes`.
Adding an admin screen: subclass AdminPage, add one entry in AdminWebController.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.config.settings import AppSettings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.routing import BaseController
from app.domain.models import MenuArea, Role
from app.infrastructure.auth.manager import BaseAuthHandler, CurrentUser
from app.repositories.log_repository import LogRepository
from app.scheduler.engine import SchedulerEngine
from app.services.backup_service import BackupService
from app.services.contact_service import ContactService
from app.services.content_service import ContentService
from app.services.dashboard_service import DashboardService
from app.services.media_service import MediaService
from app.services.menu_service import MenuService
from app.services.search_service import SearchService
from app.services.site_settings_service import SiteSettingsService
from app.services.system_service import SystemService
from app.services.user_service import UserService
from app.web.pages.admin import (
    AdminHomePage,
    AdminLoginPage,
    AdminPasswordChangePage,
    BackupManagerPage,
    ContactMessagesPage,
    ContentManagementPage,
    DashboardPage,
    DbConnectionManagementPage,
    JobsMonitorPage,
    LogManagementPage,
    MediaManagerPage,
    MenuManagementPage,
    SessionManagerPage,
    SettingsPage,
    UserManagementPage,
)
from app.web.pages.base import BasePage, PageContext
from app.web.pages.public import (
    AboutPage,
    ContactPage,
    EditorialPolicyPage,
    HomePage,
    IntroductionPage,
    PrivacyPolicyPage,
    SearchPage,
    SitemapPage,
    TermsPage,
)

PageFactory = Callable[[PageContext], BasePage]


class PublicWebController(BaseController):
    tags = ["public-web"]

    def __init__(
        self,
        settings: AppSettings,
        menus: MenuService,
        contents: ContentService,
        search: SearchService,
    ) -> None:
        self._settings = settings
        self._menus = menus
        self._contents = contents
        self._search = search

    def _page_routes(self) -> dict[str, PageFactory]:
        contents = self._contents
        return {
            "/": lambda ctx: HomePage(ctx, contents),
            "/about": lambda ctx: AboutPage(ctx, contents),
            "/introduction": lambda ctx: IntroductionPage(ctx, contents),
            "/contact": lambda ctx: ContactPage(ctx, contents),
            "/privacy-policy": lambda ctx: PrivacyPolicyPage(ctx, contents),
            "/terms": lambda ctx: TermsPage(ctx, contents),
            "/editorial-policy": lambda ctx: EditorialPolicyPage(ctx, contents),
            "/sitemap": lambda ctx: SitemapPage(ctx, contents),
            "/search": lambda ctx: SearchPage(ctx, self._search),
        }

    def _context(self, request: Request) -> PageContext:
        return PageContext(
            site_name=self._settings.name,
            path=request.url.path,
            menu_items=self._menus.get_menu(MenuArea.PUBLIC),
            query=dict(request.query_params),
            csp_nonce=getattr(request.state, "csp_nonce", ""),
        )

    def _register(self, router: APIRouter) -> None:
        for path, factory in self._page_routes().items():
            self._add_page_route(router, path, factory)

        @router.get("/rss", include_in_schema=False)
        def rss() -> Response:
            return Response(content=self._build_rss(), media_type="application/rss+xml")

        @router.get("/sitemap.xml", include_in_schema=False)
        def sitemap_xml() -> Response:
            return Response(content=self._build_sitemap_xml(), media_type="application/xml")

        @router.get("/robots.txt", include_in_schema=False)
        def robots(request: Request) -> Response:
            lines = [
                "User-agent: *",
                "Disallow: /admin",
                "Disallow: /api/",
                f"Sitemap: {str(request.base_url).rstrip('/')}/sitemap.xml",
            ]
            return Response(content="\n".join(lines) + "\n", media_type="text/plain")

    def _add_page_route(self, router: APIRouter, path: str, factory: PageFactory) -> None:
        # Bind per-route via default args (avoids the closure-in-loop pitfall).
        def handler(request: Request, factory: PageFactory = factory) -> HTMLResponse:
            page = factory(self._context(request))
            return HTMLResponse(page.render())

        router.add_api_route(path, handler, methods=["GET"],
                             response_class=HTMLResponse, include_in_schema=False)

    def _build_rss(self) -> str:
        items = "".join(
            f"<item><title>{xml_escape(i.title)}</title>"
            f"<link>/{xml_escape(i.slug)}</link>"
            f"<description>{xml_escape(i.summary)}</description></item>"
            for i in self._contents.rss_items()
        )
        return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
                f"<title>{xml_escape(self._settings.name)}</title><link>/</link>"
                f"<description>Latest content</description>{items}</channel></rss>")

    def _build_sitemap_xml(self) -> str:
        urls = "".join(
            f"<url><loc>{xml_escape(e['loc'])}</loc></url>"
            for e in self._contents.sitemap_entries()
        )
        return ('<?xml version="1.0" encoding="UTF-8"?>'
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>')


@dataclass(frozen=True)
class AdminWebDeps:
    """Service bundle for the admin area — one new screen = one new field
    here instead of another constructor parameter."""

    menus: MenuService
    users: UserService
    dashboard: DashboardService
    system: SystemService
    logs: LogRepository
    contents: ContentService
    site_settings: SiteSettingsService
    contact: ContactService
    media: MediaService
    backups: BackupService
    engine: SchedulerEngine | None = None


class MediaWebController(BaseController):
    """Serves uploaded media files. Registered for both the public site
    (content images) and the admin area (previews)."""

    tags = ["media"]

    def __init__(self, media: MediaService) -> None:
        self._media = media

    def _register(self, router: APIRouter) -> None:
        @router.get("/media/{name}", include_in_schema=False)
        def serve_media(name: str) -> FileResponse:
            path = self._media.resolve_path(name)   # strict name validation
            return FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


class AdminWebController(BaseController):
    prefix = "/admin"
    tags = ["admin-web"]

    def __init__(self, settings: AppSettings, auth_handler: BaseAuthHandler,
                 deps: AdminWebDeps) -> None:
        self._settings = settings
        self._auth = auth_handler
        self._deps = deps

    def _context(self, request: Request, user: CurrentUser | None) -> PageContext:
        return PageContext(
            site_name=self._settings.name,
            path=request.url.path,
            menu_items=self._deps.menus.get_menu(MenuArea.ADMIN) if user else (),
            query=dict(request.query_params),
            user=user,
            csp_nonce=getattr(request.state, "csp_nonce", ""),
        )

    def _authenticated_admin(self, request: Request) -> CurrentUser:
        user = self._auth.authenticate_request(request)
        if not user.has_role(Role.ADMIN.value):
            raise AuthorizationError("Admin role required")
        return user

    def _register(self, router: APIRouter) -> None:
        @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
        def login_page(request: Request) -> HTMLResponse:
            return HTMLResponse(AdminLoginPage(self._context(request, None)).render())

        # change-password stays reachable while the must-change flag is set.
        self._add_protected_route(router, "/change-password",
                                  lambda ctx: AdminPasswordChangePage(ctx),
                                  allow_pending_password=True)

        deps = self._deps
        protected: dict[str, PageFactory] = {
            "": lambda ctx: AdminHomePage(ctx),
            "/dashboard": lambda ctx: DashboardPage(ctx, deps.dashboard),
            "/users": lambda ctx: UserManagementPage(ctx, deps.users),
            "/menus": lambda ctx: MenuManagementPage(ctx, deps.menus),
            "/contents": lambda ctx: ContentManagementPage(ctx, deps.contents),
            "/messages": lambda ctx: ContactMessagesPage(ctx, deps.contact),
            "/media": lambda ctx: MediaManagerPage(ctx, deps.media),
            "/jobs": lambda ctx: JobsMonitorPage(ctx, deps.engine),
            "/settings": lambda ctx: SettingsPage(ctx, deps.site_settings),
            "/sessions": lambda ctx: SessionManagerPage(ctx, deps.users, deps.logs),
            "/backups": lambda ctx: BackupManagerPage(ctx, deps.backups),
            "/logs": lambda ctx: LogManagementPage(ctx, deps.logs),
            "/db-connections": lambda ctx: DbConnectionManagementPage(ctx, deps.system),
        }
        for path, factory in protected.items():
            self._add_protected_route(router, path, factory)

    def _add_protected_route(self, router: APIRouter, path: str, factory: PageFactory,
                             allow_pending_password: bool = False) -> None:
        def handler(request: Request, factory: PageFactory = factory):
            try:
                user = self._authenticated_admin(request)
            except (AuthenticationError, AuthorizationError):
                return RedirectResponse("/admin/login", status_code=303)
            if user.must_change_password and not allow_pending_password:
                return RedirectResponse("/admin/change-password", status_code=303)
            page = factory(self._context(request, user))
            return HTMLResponse(page.render())

        router.add_api_route(path, handler, methods=["GET"],
                             response_class=HTMLResponse, include_in_schema=False)
