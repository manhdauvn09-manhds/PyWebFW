"""Web (HTML) controllers — bind URLs to Page objects.

Adding a public screen: subclass PublicPage, add one entry to `_page_routes`.
Adding an admin screen: subclass AdminPage, add one entry in AdminWebController.
"""
from __future__ import annotations

from typing import Callable
from xml.sax.saxutils import escape as xml_escape

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.config.settings import AppSettings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.routing import BaseController
from app.domain.models import MenuArea, Role
from app.infrastructure.auth.manager import BaseAuthHandler, CurrentUser
from app.repositories.log_repository import LogRepository
from app.services.content_service import ContentService
from app.services.dashboard_service import DashboardService
from app.services.menu_service import MenuService
from app.services.search_service import SearchService
from app.services.system_service import SystemService
from app.services.user_service import UserService
from app.web.pages.admin import (
    AdminHomePage,
    AdminLoginPage,
    AdminPasswordChangePage,
    ContentManagementPage,
    DashboardPage,
    DbConnectionManagementPage,
    LogManagementPage,
    MenuManagementPage,
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


class AdminWebController(BaseController):
    prefix = "/admin"
    tags = ["admin-web"]

    def __init__(
        self,
        settings: AppSettings,
        auth_handler: BaseAuthHandler,
        menus: MenuService,
        users: UserService,
        dashboard: DashboardService,
        system: SystemService,
        logs: LogRepository,
        contents: ContentService,
    ) -> None:
        self._settings = settings
        self._auth = auth_handler
        self._menus = menus
        self._users = users
        self._dashboard = dashboard
        self._system = system
        self._logs = logs
        self._contents = contents

    def _context(self, request: Request, user: CurrentUser | None) -> PageContext:
        return PageContext(
            site_name=self._settings.name,
            path=request.url.path,
            menu_items=self._menus.get_menu(MenuArea.ADMIN) if user else (),
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

        protected: dict[str, PageFactory] = {
            "": lambda ctx: AdminHomePage(ctx),
            "/dashboard": lambda ctx: DashboardPage(ctx, self._dashboard),
            "/users": lambda ctx: UserManagementPage(ctx, self._users),
            "/menus": lambda ctx: MenuManagementPage(ctx, self._menus),
            "/contents": lambda ctx: ContentManagementPage(ctx, self._contents),
            "/logs": lambda ctx: LogManagementPage(ctx, self._logs),
            "/db-connections": lambda ctx: DbConnectionManagementPage(ctx, self._system),
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
