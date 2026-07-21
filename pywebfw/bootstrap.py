"""Composition root (Community Edition).

`ApplicationBuilder` is the ONLY place that knows concrete wiring:
settings -> container registrations -> schema -> controllers -> middleware ->
scheduler -> FastAPI app. Every other layer depends on abstractions.

Applications extend the framework by passing `AppModule` plugins — each hook
(services, schema, controllers, jobs, events) runs at the matching startup
phase, so projects never edit framework code.

PostgreSQL/Redis backends, traffic analytics, backups, redirects, media,
sessions, 2FA and CSV export are part of PyWebFW Pro.
"""
from __future__ import annotations

import contextlib
import time
from typing import AsyncIterator, Sequence

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from pywebfw.api.admin_api import (
    AdminAuthApiController,
    AdminContactApiController,
    AdminContentApiController,
    AdminDashboardApiController,
    AdminLogApiController,
    AdminMenuApiController,
    AdminRedirectApiController,
    AdminSettingsApiController,
    AdminSystemApiController,
    AdminUserApiController,
)
from pywebfw.api.public_api import PublicApiController
from pywebfw.config.settings import (
    MODULE_ADMIN,
    MODULE_PUBLIC,
    MODULE_SCHEDULER,
    AppSettings,
    get_settings,
)
from pywebfw.core.container import ServiceContainer
from pywebfw.core.events import DomainEvent, EventBus
from pywebfw.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    FrameworkError,
)
from pywebfw.core.logging import LoggerFactory
from pywebfw.core.middleware import (
    MaintenanceMiddleware,
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from pywebfw.core.responses import ApiResponse
from pywebfw.core.routing import BaseController
from pywebfw.core.security import PasswordHasher, SlidingWindowRateLimiter, TokenManager
from pywebfw.infrastructure.auth.manager import BaseAuthHandler, TokenAuthHandler
from pywebfw.infrastructure.cache.manager import BaseCacheManager, InMemoryCacheManager
from pywebfw.infrastructure.database.manager import BaseDatabaseManager, SQLiteDatabaseManager
from pywebfw.infrastructure.database.schema import SchemaInitializer
from pywebfw.infrastructure.mail.mailer import BaseMailer, NullMailer, SmtpMailer
from pywebfw.repositories.contact_repository import ContactRepository
from pywebfw.repositories.content_repository import ContentRepository
from pywebfw.repositories.db_connection_repository import DbConnectionRepository
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.repositories.menu_repository import MenuRepository
from pywebfw.repositories.redirect_repository import RedirectRepository
from pywebfw.repositories.setting_repository import SettingRepository
from pywebfw.repositories.user_repository import UserRepository
from pywebfw.scheduler.engine import JobRegistry, SchedulerEngine
from pywebfw.scheduler.jobs import (
    CacheWarmupJob,
    DatabaseHealthCheckJob,
    DatabaseOptimizeJob,
    IdleConnectionCloserJob,
    LogCleanupJob,
    ServerHealthCheckJob,
)
from pywebfw.plugins import AppModule
from pywebfw.services.auth_service import AuthService
from pywebfw.services.contact_service import ContactService
from pywebfw.services.content_service import ContentService
from pywebfw.services.dashboard_service import DashboardService
from pywebfw.services.menu_service import MenuService
from pywebfw.services.redirect_service import RedirectService
from pywebfw.services.search_service import SearchService
from pywebfw.services.site_settings_service import SiteSettingsService
from pywebfw.services.system_service import (
    DatabaseHealthChecker,
    ServerHealthChecker,
    SystemService,
)
from pywebfw.services.user_service import UserService
from pywebfw.web.controllers import (
    AdminWebController,
    AdminWebDeps,
    DynamicContentController,
    PublicWebController,
)
from pywebfw.web.error_pages import render_error_page

_PRO_HINT = ("is available in PyWebFW Pro — "
             "see https://github.com/manhdauvn09-manhds/PyWebFW")


class ApplicationBuilder:
    def __init__(self, settings: AppSettings | None = None,
                 plugins: Sequence[AppModule] = ()) -> None:
        self._settings = settings or get_settings()
        self._container = ServiceContainer()
        self._plugins = list(plugins)

    # --- public API -----------------------------------------------------------
    def build_app(self) -> FastAPI:
        LoggerFactory.configure(debug=self._settings.debug)
        self._register_infrastructure()
        self._register_repositories()
        self._register_services()
        for plugin in self._plugins:
            plugin.register_services(self._container, self._settings)
        self._register_event_handlers()
        self._initialize_schema()
        engine = self._build_scheduler()

        app = FastAPI(title=self._settings.name, lifespan=self._lifespan(engine),
                      docs_url="/api/docs" if self._settings.debug else None)
        app.state.container = self._container
        app.state.scheduler_engine = engine
        self._register_middleware(app)
        self._register_error_handlers(app)
        # /healthz must be routed before the public catch-all /{slug}.
        self._register_health_endpoint(app, engine)
        self._register_controllers(app, engine)
        return app

    def _register_event_handlers(self) -> None:
        """Domain-event subscribers — the only place that knows which side
        effects follow which facts."""
        c = self._container
        bus = c.resolve(EventBus)
        settings = self._settings

        def notify_admin_of_contact(event: DomainEvent) -> None:
            if not settings.mail.admin_email:
                return
            payload = event.payload
            c.resolve(BaseMailer).send(
                settings.mail.admin_email,
                f"[Contact] {payload['subject'] or 'New message'} — {payload['name']}",
                f"From: {payload['name']} <{payload['email']}>\n\n{payload['message']}",
            )

        def alert_failed_job(event: DomainEvent) -> None:
            if not settings.mail.admin_email:
                return
            payload = event.payload
            c.resolve(BaseMailer).send(
                settings.mail.admin_email,
                f"[Job FAILED] {payload['job']} on {settings.name}",
                (f"Job: {payload['job']}\nStarted: {payload['started_at']}\n"
                 f"Attempts: {payload['attempts']}\nError: {payload['error']}"),
            )

        def auto_redirect_on_slug_change(event: DomainEvent) -> None:
            payload = event.payload
            c.resolve(RedirectService).auto_create(
                payload["old_path"], payload["new_path"], actor="system")

        bus.subscribe("contact.submitted", notify_admin_of_contact)
        bus.subscribe("job.failed", alert_failed_job)
        bus.subscribe("content.slug_changed", auto_redirect_on_slug_change)
        for plugin in self._plugins:
            plugin.subscribe_events(bus, c)

    # --- wiring ---------------------------------------------------------------
    def _register_infrastructure(self) -> None:
        c = self._container
        settings = self._settings
        c.register_instance(AppSettings, settings)
        c.register_singleton(BaseDatabaseManager, lambda c: self._build_database())
        c.register_singleton(BaseCacheManager, lambda c: self._build_cache())
        c.register_instance(PasswordHasher, PasswordHasher(settings.security.password_iterations))
        c.register_instance(TokenManager, TokenManager(
            settings.security.secret_key, settings.security.token_ttl_seconds))
        c.register_instance(EventBus, EventBus())
        c.register_singleton(BaseAuthHandler, lambda c: TokenAuthHandler(
            c.resolve(TokenManager), c.resolve(UserRepository)))
        c.register_singleton(BaseMailer, lambda c: (
            SmtpMailer(settings.mail) if settings.mail.host else NullMailer()))

    def _build_database(self) -> BaseDatabaseManager:
        db = self._settings.database
        if db.driver != "sqlite":
            raise ConfigurationError(
                f"The '{db.driver}' database backend {_PRO_HINT}")
        return SQLiteDatabaseManager(db.path, db.pool_size, LoggerFactory.get("db"))

    def _build_cache(self) -> BaseCacheManager:
        cache = self._settings.cache
        if cache.backend != "memory":
            raise ConfigurationError(
                f"The '{cache.backend}' cache backend {_PRO_HINT}")
        return InMemoryCacheManager(cache.default_ttl_seconds)

    def _register_repositories(self) -> None:
        c = self._container
        for repo_type in (UserRepository, MenuRepository, LogRepository,
                          ContentRepository, DbConnectionRepository,
                          SettingRepository, ContactRepository,
                          RedirectRepository):
            c.register_singleton(
                repo_type,
                lambda c, rt=repo_type: rt(c.resolve(BaseDatabaseManager)))

    def _register_services(self) -> None:
        c = self._container
        c.register_singleton(AuthService, lambda c: AuthService(
            c.resolve(UserRepository), c.resolve(LogRepository),
            c.resolve(PasswordHasher), c.resolve(TokenManager)))
        c.register_singleton(UserService, lambda c: UserService(
            c.resolve(BaseDatabaseManager), c.resolve(UserRepository),
            c.resolve(LogRepository), c.resolve(PasswordHasher)))
        c.register_singleton(MenuService, lambda c: MenuService(
            c.resolve(MenuRepository), c.resolve(LogRepository), c.resolve(BaseCacheManager)))
        c.register_singleton(ContentService, lambda c: ContentService(
            c.resolve(ContentRepository), c.resolve(BaseCacheManager),
            c.resolve(LogRepository), c.resolve(EventBus)))
        c.register_singleton(SearchService, lambda c: SearchService(
            c.resolve(ContentRepository)))
        c.register_singleton(ContactService, lambda c: ContactService(
            c.resolve(ContactRepository), c.resolve(LogRepository),
            c.resolve(EventBus)))
        c.register_singleton(SiteSettingsService, lambda c: SiteSettingsService(
            c.resolve(SettingRepository), c.resolve(BaseCacheManager),
            c.resolve(LogRepository)))
        c.register_singleton(RedirectService, lambda c: RedirectService(
            c.resolve(RedirectRepository), c.resolve(BaseCacheManager),
            c.resolve(LogRepository)))
        c.register_singleton(DashboardService, lambda c: DashboardService(
            c.resolve(BaseDatabaseManager), c.resolve(UserRepository),
            c.resolve(LogRepository), c.resolve(ContentRepository),
            c.resolve(BaseCacheManager)))
        c.register_instance(ServerHealthChecker, ServerHealthChecker(started_at=time.time()))
        c.register_singleton(SystemService, lambda c: SystemService(
            c.resolve(DbConnectionRepository), c.resolve(LogRepository),
            checkers=[c.resolve(ServerHealthChecker),
                      DatabaseHealthChecker(c.resolve(BaseDatabaseManager))]))

    def _initialize_schema(self) -> None:
        db = self._container.resolve(BaseDatabaseManager)
        SchemaInitializer(
            db,
            self._container.resolve(PasswordHasher),
            LoggerFactory.get("schema"),
        ).ensure()
        for plugin in self._plugins:
            plugin.init_schema(db)

    def _build_scheduler(self) -> SchedulerEngine | None:
        """The scheduler only exists when its module is deployed in this
        process — a dedicated scheduler container runs it alone, while web
        containers skip it entirely."""
        if not (self._settings.has_module(MODULE_SCHEDULER)
                and self._settings.scheduler.enabled):
            return None
        c = self._container
        db = c.resolve(BaseDatabaseManager)
        cache = c.resolve(BaseCacheManager)
        registry = JobRegistry()
        registry.register(ServerHealthCheckJob(c.resolve(ServerHealthChecker)))
        registry.register(DatabaseHealthCheckJob(db))
        registry.register(LogCleanupJob(c.resolve(LogRepository)))
        registry.register(CacheWarmupJob(c.resolve(MenuService), cache))
        registry.register(DatabaseOptimizeJob(db))
        registry.register(IdleConnectionCloserJob(
            db, self._settings.database.idle_timeout_seconds))
        for plugin in self._plugins:
            for job in plugin.jobs(c):
                registry.register(job)
        return SchedulerEngine(registry, self._settings.scheduler.tick_seconds,
                               audit_logs=c.resolve(LogRepository),
                               events=c.resolve(EventBus))

    def _lifespan(self, engine: SchedulerEngine | None):
        container = self._container

        @contextlib.asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            if engine is not None:
                await engine.start()
            try:
                yield
            finally:
                if engine is not None:
                    await engine.stop()
                container.resolve(BaseDatabaseManager).shutdown()

        return lifespan

    def _register_middleware(self, app: FastAPI) -> None:
        rl = self._settings.rate_limit
        limiter = SlidingWindowRateLimiter(rl.max_requests, rl.window_seconds)
        login_limiter = SlidingWindowRateLimiter(rl.login_max_requests, rl.login_window_seconds)
        app.add_middleware(MaintenanceMiddleware,
                           site_settings=self._container.resolve(SiteSettingsService))
        app.add_middleware(RequestLoggingMiddleware)
        app.add_middleware(RateLimitMiddleware, limiter=limiter, login_limiter=login_limiter)
        app.add_middleware(SecurityHeadersMiddleware)

    def _register_error_handlers(self, app: FastAPI) -> None:
        def _nonce(request: Request) -> str:
            return getattr(request.state, "csp_nonce", "")

        def _is_api(request: Request) -> bool:
            return request.url.path.startswith("/api/")

        async def framework_error_handler(request: Request, exc: FrameworkError):
            if _is_api(request):
                return JSONResponse(
                    status_code=exc.status_code,
                    content=ApiResponse.fail(exc.error_code, exc.message, exc.details).to_dict(),
                )
            if isinstance(exc, AuthenticationError) and request.url.path.startswith("/admin"):
                return RedirectResponse("/admin/login", status_code=303)
            return HTMLResponse(
                render_error_page(exc.status_code, exc.message, nonce=_nonce(request)),
                status_code=exc.status_code,
            )

        async def http_exception_handler(request: Request, exc: StarletteHTTPException):
            """Routing-level errors (404 unknown path, 405...) — JSON envelope
            for API paths, styled page for the web."""
            if _is_api(request):
                return JSONResponse(
                    status_code=exc.status_code,
                    content=ApiResponse.fail("HTTP_ERROR", str(exc.detail)).to_dict(),
                )
            return HTMLResponse(
                render_error_page(exc.status_code, nonce=_nonce(request)),
                status_code=exc.status_code,
            )

        async def validation_error_handler(request: Request, exc: RequestValidationError):
            """Pydantic boundary errors share the standard envelope."""
            details = [{"field": ".".join(str(p) for p in err["loc"][1:]) or "body",
                        "message": err["msg"]} for err in exc.errors()]
            return JSONResponse(
                status_code=422,
                content=ApiResponse.fail("VALIDATION_FAILED", "Validation failed",
                                         details).to_dict(),
            )

        app.add_exception_handler(FrameworkError, framework_error_handler)
        app.add_exception_handler(StarletteHTTPException, http_exception_handler)
        app.add_exception_handler(RequestValidationError, validation_error_handler)

    def _register_controllers(self, app: FastAPI,
                              engine: SchedulerEngine | None = None) -> None:
        """Mounts only the controllers of the modules deployed in this process
        (APP_MODULES) — a scheduler-only container exposes no web routes."""
        c = self._container
        settings = self._settings
        controllers: list[BaseController] = []

        if settings.has_module(MODULE_PUBLIC):
            controllers += [
                PublicWebController(settings, c.resolve(MenuService),
                                    c.resolve(ContentService), c.resolve(SearchService)),
                PublicApiController(c.resolve(MenuService), c.resolve(ContentService),
                                    c.resolve(SearchService), c.resolve(ContactService)),
            ]

        if settings.has_module(MODULE_ADMIN):
            auth_handler = c.resolve(BaseAuthHandler)
            auth_service = c.resolve(AuthService)
            deps = AdminWebDeps(
                menus=c.resolve(MenuService),
                users=c.resolve(UserService),
                dashboard=c.resolve(DashboardService),
                system=c.resolve(SystemService),
                logs=c.resolve(LogRepository),
                contents=c.resolve(ContentService),
                site_settings=c.resolve(SiteSettingsService),
                contact=c.resolve(ContactService),
                redirects=c.resolve(RedirectService),
                engine=engine,
            )
            controllers += [
                AdminWebController(settings, auth_handler, deps),
                AdminAuthApiController(auth_handler, auth_service,
                                       cookie_secure=settings.is_production),
                AdminUserApiController(auth_handler, auth_service, c.resolve(UserService)),
                AdminMenuApiController(auth_handler, auth_service, c.resolve(MenuService)),
                AdminContentApiController(auth_handler, auth_service,
                                          c.resolve(ContentService)),
                AdminContactApiController(auth_handler, auth_service,
                                          c.resolve(ContactService)),
                AdminLogApiController(auth_handler, auth_service, c.resolve(LogRepository)),
                AdminDashboardApiController(auth_handler, auth_service,
                                            c.resolve(DashboardService)),
                AdminSettingsApiController(auth_handler, auth_service,
                                           c.resolve(SiteSettingsService)),
                AdminSystemApiController(auth_handler, auth_service,
                                         c.resolve(SystemService), engine),
                AdminRedirectApiController(auth_handler, auth_service,
                                           c.resolve(RedirectService)),
            ]

        # Plugin controllers mount after the built-ins...
        for plugin in self._plugins:
            controllers += plugin.controllers(c, settings)

        # ...and the /{slug} catch-all MUST be the very last route registered.
        if settings.has_module(MODULE_PUBLIC):
            controllers.append(DynamicContentController(
                settings, c.resolve(MenuService), c.resolve(ContentService),
                redirects=c.resolve(RedirectService)))

        for controller in controllers:
            app.include_router(controller.build_router())

    def _register_health_endpoint(self, app: FastAPI,
                                  engine: SchedulerEngine | None) -> None:
        """Liveness/readiness probe for Docker healthchecks and load balancers
        — present in every deployment mode."""
        settings = self._settings
        container = self._container

        @app.get("/healthz", include_in_schema=False)
        def healthz() -> dict:
            db = container.resolve(BaseDatabaseManager).health_check()
            body: dict = {
                "status": "ok" if db["healthy"] else "degraded",
                "modules": sorted(settings.modules),
                "database": db,
            }
            if engine is not None:
                body["scheduler"] = engine.status_report
            return body
