"""Composition root.

`ApplicationBuilder` is the ONLY place that knows concrete wiring:
settings -> container registrations -> schema -> controllers -> middleware ->
scheduler -> FastAPI app. Every other layer depends on abstractions.
"""
from __future__ import annotations

import contextlib
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api.admin_api import (
    AdminAuthApiController,
    AdminContentApiController,
    AdminDashboardApiController,
    AdminLogApiController,
    AdminMenuApiController,
    AdminSystemApiController,
    AdminUserApiController,
)
from app.api.public_api import PublicApiController
from app.config.settings import (
    MODULE_ADMIN,
    MODULE_PUBLIC,
    MODULE_SCHEDULER,
    AppSettings,
    get_settings,
)
from app.core.container import ServiceContainer
from app.core.exceptions import AuthenticationError, FrameworkError
from app.core.logging import LoggerFactory
from app.core.middleware import (
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from app.core.responses import ApiResponse
from app.core.routing import BaseController
from app.core.security import PasswordHasher, SlidingWindowRateLimiter, TokenManager
from app.infrastructure.auth.manager import BaseAuthHandler, TokenAuthHandler
from app.infrastructure.cache.manager import BaseCacheManager, InMemoryCacheManager
from app.infrastructure.database.manager import BaseDatabaseManager, SQLiteDatabaseManager
from app.infrastructure.database.schema import SchemaInitializer
from app.repositories.content_repository import ContentRepository
from app.repositories.db_connection_repository import DbConnectionRepository
from app.repositories.log_repository import LogRepository
from app.repositories.menu_repository import MenuRepository
from app.repositories.user_repository import UserRepository
from app.scheduler.engine import JobRegistry, SchedulerEngine
from app.scheduler.jobs import (
    CacheWarmupJob,
    DatabaseBackupJob,
    DatabaseHealthCheckJob,
    DatabaseOptimizeJob,
    IdleConnectionCloserJob,
    LogCleanupJob,
    ServerHealthCheckJob,
)
from app.services.auth_service import AuthService
from app.services.content_service import ContentService
from app.services.dashboard_service import DashboardService
from app.services.menu_service import MenuService
from app.services.search_service import SearchService
from app.services.system_service import (
    DatabaseHealthChecker,
    ServerHealthChecker,
    SystemService,
)
from app.services.user_service import UserService
from app.web.controllers import AdminWebController, PublicWebController


class ApplicationBuilder:
    def __init__(self, settings: AppSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._container = ServiceContainer()

    # --- public API -----------------------------------------------------------
    def build_app(self) -> FastAPI:
        LoggerFactory.configure(debug=self._settings.debug)
        self._register_infrastructure()
        self._register_repositories()
        self._register_services()
        self._initialize_schema()
        engine = self._build_scheduler()

        app = FastAPI(title=self._settings.name, lifespan=self._lifespan(engine),
                      docs_url="/api/docs" if self._settings.debug else None)
        app.state.container = self._container
        app.state.scheduler_engine = engine
        self._register_middleware(app)
        self._register_error_handlers(app)
        self._register_controllers(app)
        self._register_health_endpoint(app, engine)
        return app

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
        c.register_singleton(BaseAuthHandler, lambda c: TokenAuthHandler(
            c.resolve(TokenManager), c.resolve(UserRepository)))

    def _build_database(self) -> BaseDatabaseManager:
        db = self._settings.database
        if db.driver == "postgres":
            from app.infrastructure.database.manager import PostgresDatabaseManager
            return PostgresDatabaseManager(db.dsn, db.pool_size, LoggerFactory.get("db"))
        return SQLiteDatabaseManager(db.path, db.pool_size, LoggerFactory.get("db"))

    def _build_cache(self) -> BaseCacheManager:
        cache = self._settings.cache
        if cache.backend == "redis":
            from app.infrastructure.cache.manager import RedisCacheManager
            return RedisCacheManager(cache.redis_url, cache.default_ttl_seconds)
        return InMemoryCacheManager(cache.default_ttl_seconds)

    def _register_repositories(self) -> None:
        c = self._container
        for repo_type in (UserRepository, MenuRepository, LogRepository,
                          ContentRepository, DbConnectionRepository):
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
            c.resolve(LogRepository)))
        c.register_singleton(SearchService, lambda c: SearchService(
            c.resolve(ContentRepository)))
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
        SchemaInitializer(
            self._container.resolve(BaseDatabaseManager),
            self._container.resolve(PasswordHasher),
            LoggerFactory.get("schema"),
        ).ensure()

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
        registry.register(DatabaseBackupJob(db, self._settings.database.path))
        registry.register(IdleConnectionCloserJob(
            db, self._settings.database.idle_timeout_seconds))
        return SchedulerEngine(registry, self._settings.scheduler.tick_seconds,
                               audit_logs=c.resolve(LogRepository))

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
        app.add_middleware(RequestLoggingMiddleware)
        app.add_middleware(RateLimitMiddleware, limiter=limiter, login_limiter=login_limiter)
        app.add_middleware(SecurityHeadersMiddleware)

    @staticmethod
    def _register_error_handlers(app: FastAPI) -> None:
        async def framework_error_handler(request: Request, exc: FrameworkError):
            if request.url.path.startswith("/api/"):
                return JSONResponse(
                    status_code=exc.status_code,
                    content=ApiResponse.fail(exc.error_code, exc.message, exc.details).to_dict(),
                )
            if isinstance(exc, AuthenticationError) and request.url.path.startswith("/admin"):
                return RedirectResponse("/admin/login", status_code=303)
            return HTMLResponse(
                f"<h1>{exc.status_code}</h1><p>{exc.message}</p>",
                status_code=exc.status_code,
            )

        app.add_exception_handler(FrameworkError, framework_error_handler)

    def _register_controllers(self, app: FastAPI) -> None:
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
                                    c.resolve(SearchService)),
            ]

        if settings.has_module(MODULE_ADMIN):
            auth_handler = c.resolve(BaseAuthHandler)
            auth_service = c.resolve(AuthService)
            controllers += [
                AdminWebController(settings, auth_handler, c.resolve(MenuService),
                                   c.resolve(UserService), c.resolve(DashboardService),
                                   c.resolve(SystemService), c.resolve(LogRepository),
                                   c.resolve(ContentService)),
                AdminAuthApiController(auth_handler, auth_service,
                                       cookie_secure=settings.is_production),
                AdminUserApiController(auth_handler, auth_service, c.resolve(UserService)),
                AdminMenuApiController(auth_handler, auth_service, c.resolve(MenuService)),
                AdminContentApiController(auth_handler, auth_service,
                                          c.resolve(ContentService)),
                AdminLogApiController(auth_handler, auth_service, c.resolve(LogRepository)),
                AdminDashboardApiController(auth_handler, auth_service,
                                            c.resolve(DashboardService)),
                AdminSystemApiController(auth_handler, auth_service,
                                         c.resolve(SystemService)),
            ]

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
