"""Admin JSON API.

One controller class per resource, all inheriting `AdminApiController`, which
centralizes the RBAC guard. Adding a new admin resource = one new subclass.

Auth model: login returns a bearer token AND sets it as an HttpOnly,
SameSite=Strict cookie so the server-rendered admin pages stay authenticated.
SPA/automation clients use the `Authorization: Bearer` header instead.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, EmailStr, Field

from pywebfw.core.exceptions import ConflictError, NotFoundError, SchedulerError
from pywebfw.core.pagination import PageRequest
from pywebfw.core.routing import BaseApiController
from pywebfw.scheduler.engine import SchedulerEngine
from pywebfw.domain.models import ContentItem, DbConnectionProfile, MenuArea, MenuItem, Role
from pywebfw.infrastructure.auth.manager import (
    ADMIN_TOKEN_COOKIE,
    BaseAuthHandler,
    CurrentUser,
    RoleGuard,
)
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.services.auth_service import AuthService
from pywebfw.services.contact_service import ContactService
from pywebfw.services.content_service import ContentService
from pywebfw.services.dashboard_service import DashboardService
from pywebfw.services.menu_service import MenuService
from pywebfw.services.site_settings_service import SiteSettingsService
from pywebfw.services.system_service import SystemService
from pywebfw.services.user_service import UserInput, UserService


# --- request DTOs (shape validation at the boundary, via Pydantic) ----------
class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class UserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str | None = Field(default=None, max_length=128)
    role: str = Role.VIEWER.value
    is_active: bool = True


class MenuRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=300)
    area: MenuArea = MenuArea.PUBLIC
    parent_id: int | None = None
    position: int = 0
    is_active: bool = True


class ContentRequest(BaseModel):
    slug: str = Field(min_length=2, max_length=100,
                      pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field("", max_length=500)
    body: str = Field("", max_length=50_000)
    seo_title: str = Field("", max_length=200)
    seo_description: str = Field("", max_length=300)
    is_published: bool = True


class SettingsUpdateRequest(BaseModel):
    values: dict[str, str] = Field(min_length=1)


class DbConnectionRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    driver: str = "sqlite"
    dsn: str = Field(min_length=1, max_length=500)
    pool_size: int = Field(5, ge=1, le=100)
    idle_timeout_seconds: int = Field(300, ge=10)
    is_default: bool = False


class AdminApiController(BaseApiController):
    """Base for every admin resource controller: shared prefix + RBAC guard.
    Users flagged `must_change_password` are blocked from every resource
    except the auth endpoints, until they set a new password."""

    tags = ["admin-api"]
    required_roles: tuple[str, ...] = (Role.ADMIN.value,)
    enforce_password_change: bool = True

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService) -> None:
        self._guard = RoleGuard(auth_handler, *self.required_roles,
                                enforce_password_change=self.enforce_password_change)
        self._auth_service = auth_service

    def _actor_name(self, user: CurrentUser) -> str:
        return self._auth_service.get_user(user.id).username


class AdminAuthApiController(AdminApiController):
    prefix = "/api/admin/auth"
    # Auth endpoints stay reachable while a password change is pending.
    enforce_password_change = False

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 cookie_secure: bool = False) -> None:
        super().__init__(auth_handler, auth_service)
        # True in production: cookie only ever travels over HTTPS.
        self._cookie_secure = cookie_secure

    def _set_session_cookie(self, response: Response, token: str) -> None:
        response.set_cookie(
            ADMIN_TOKEN_COOKIE, token,
            httponly=True, samesite="strict", path="/",
            secure=self._cookie_secure,
        )

    def _register(self, router: APIRouter) -> None:
        @router.post("/login")
        def login(payload: LoginRequest, response: Response) -> dict:
            result = self._auth_service.login(payload.username, payload.password)
            self._set_session_cookie(response, result.token)
            return self.ok({"token": result.token, "user": result.user.to_public_dict()})

        @router.post("/change-password")
        def change_password(payload: ChangePasswordRequest, response: Response,
                            user: CurrentUser = Depends(self._guard)) -> dict:
            result = self._auth_service.change_password(
                user.id, payload.current_password, payload.new_password)
            # Old tokens are revoked — hand the client its replacement.
            self._set_session_cookie(response, result.token)
            return self.ok({"token": result.token, "user": result.user.to_public_dict()})

        @router.post("/logout")
        def logout(request: Request, response: Response) -> dict:
            try:
                current = self._guard(request)
                self._auth_service.revoke_tokens(current.id)   # logout everywhere
            except Exception:
                pass   # anonymous/expired logout is still a successful logout
            response.delete_cookie(ADMIN_TOKEN_COOKIE, path="/")
            return self.ok({"logged_out": True})

        @router.get("/me")
        def me(user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._auth_service.get_user(user.id).to_public_dict())


class AdminUserApiController(AdminApiController):
    prefix = "/api/admin/users"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 users: UserService) -> None:
        super().__init__(auth_handler, auth_service)
        self._users = users

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def list_users(
            page: int = Query(1, ge=1),
            size: int = Query(20, ge=1, le=100),
            sort_by: str | None = None,
            sort_desc: bool = False,
            user: CurrentUser = Depends(self._guard),
        ) -> dict:
            result = self._users.list_users(PageRequest.create(page, size, sort_by, sort_desc))
            return self.paginated(result, lambda u: u.to_public_dict())

        @router.get("/{user_id}")
        def get_user(user_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._users.get(user_id).to_public_dict())

        @router.post("", status_code=201)
        def create_user(payload: UserRequest, user: CurrentUser = Depends(self._guard)) -> dict:
            created = self._users.create(self._to_input(payload), actor=self._actor_name(user))
            return self.ok(created.to_public_dict())

        @router.put("/{user_id}")
        def update_user(user_id: int, payload: UserRequest,
                        user: CurrentUser = Depends(self._guard)) -> dict:
            updated = self._users.update(user_id, self._to_input(payload),
                                         actor=self._actor_name(user))
            return self.ok(updated.to_public_dict())

        @router.delete("/{user_id}")
        def delete_user(user_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            self._users.delete(user_id, actor=self._actor_name(user))
            return self.ok({"deleted": user_id})

    @staticmethod
    def _to_input(payload: UserRequest) -> UserInput:
        return UserInput(username=payload.username, email=str(payload.email),
                         password=payload.password, role=payload.role,
                         is_active=payload.is_active)


class AdminMenuApiController(AdminApiController):
    prefix = "/api/admin/menus"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 menus: MenuService) -> None:
        super().__init__(auth_handler, auth_service)
        self._menus = menus

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def list_menus(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=100),
                       user: CurrentUser = Depends(self._guard)) -> dict:
            result = self._menus.list_menus(PageRequest.create(page, size))
            return self.paginated(result, lambda m: m.to_dict())

        @router.post("", status_code=201)
        def create_menu(payload: MenuRequest, user: CurrentUser = Depends(self._guard)) -> dict:
            item = MenuItem(**payload.model_dump())
            return self.ok(self._menus.create(item, self._actor_name(user)).to_dict())

        @router.put("/{menu_id}")
        def update_menu(menu_id: int, payload: MenuRequest,
                        user: CurrentUser = Depends(self._guard)) -> dict:
            item = MenuItem(id=menu_id, **payload.model_dump())
            return self.ok(self._menus.update(item, self._actor_name(user)).to_dict())

        @router.delete("/{menu_id}")
        def delete_menu(menu_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            self._menus.delete(menu_id, self._actor_name(user))
            return self.ok({"deleted": menu_id})


class AdminContentApiController(AdminApiController):
    prefix = "/api/admin/contents"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 contents: ContentService) -> None:
        super().__init__(auth_handler, auth_service)
        self._contents = contents

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def list_contents(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                          sort_by: str | None = None, sort_desc: bool = False,
                          user: CurrentUser = Depends(self._guard)) -> dict:
            result = self._contents.list_contents(
                PageRequest.create(page, size, sort_by, sort_desc))
            return self.paginated(result, lambda item: item.to_dict())

        @router.get("/{content_id}")
        def get_content(content_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._contents.get(content_id).to_dict())

        @router.post("", status_code=201)
        def create_content(payload: ContentRequest,
                           user: CurrentUser = Depends(self._guard)) -> dict:
            item = ContentItem(**payload.model_dump())
            created = self._contents.create(item, actor=self._actor_name(user))
            return self.ok(created.to_dict())

        @router.put("/{content_id}")
        def update_content(content_id: int, payload: ContentRequest,
                           user: CurrentUser = Depends(self._guard)) -> dict:
            item = ContentItem(id=content_id, **payload.model_dump())
            updated = self._contents.update(item, actor=self._actor_name(user))
            return self.ok(updated.to_dict())

        @router.delete("/{content_id}")
        def delete_content(content_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            self._contents.delete(content_id, actor=self._actor_name(user))
            return self.ok({"deleted": content_id})


class AdminContactApiController(AdminApiController):
    prefix = "/api/admin/messages"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 contact: ContactService) -> None:
        super().__init__(auth_handler, auth_service)
        self._contact = contact

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def list_messages(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                          unread_only: bool = False,
                          user: CurrentUser = Depends(self._guard)) -> dict:
            result = self._contact.list_messages(PageRequest.create(page, size),
                                                 unread_only=unread_only)
            return self.paginated(result, lambda m: m.to_dict())

        @router.post("/{message_id}/read")
        def mark_read(message_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            entry = self._contact.mark_read(message_id, actor=self._actor_name(user))
            return self.ok(entry.to_dict())

        @router.delete("/{message_id}")
        def delete_message(message_id: int,
                           user: CurrentUser = Depends(self._guard)) -> dict:
            self._contact.delete(message_id, actor=self._actor_name(user))
            return self.ok({"deleted": message_id})


class AdminLogApiController(AdminApiController):
    prefix = "/api/admin/logs"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 logs: LogRepository) -> None:
        super().__init__(auth_handler, auth_service)
        self._logs = logs

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def list_logs(
            page: int = Query(1, ge=1),
            size: int = Query(20, ge=1, le=100),
            level: str | None = Query(None, pattern="^(info|warning|error)$"),
            user: CurrentUser = Depends(self._guard),
        ) -> dict:
            where, params = ("level = ?", (level,)) if level else (None, ())
            result = self._logs.list_page(PageRequest.create(page, size), where, params)
            return self.paginated(result, lambda log: log.to_dict())

class AdminDashboardApiController(AdminApiController):
    prefix = "/api/admin/dashboard"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 dashboard: DashboardService) -> None:
        super().__init__(auth_handler, auth_service)
        self._dashboard = dashboard

    def _register(self, router: APIRouter) -> None:
        @router.get("/metrics")
        def metrics(user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._dashboard.metrics())


class AdminSettingsApiController(AdminApiController):
    prefix = "/api/admin/settings"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 site_settings: SiteSettingsService) -> None:
        super().__init__(auth_handler, auth_service)
        self._site_settings = site_settings

    def _register(self, router: APIRouter) -> None:
        @router.get("")
        def get_settings(user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._site_settings.all())

        @router.put("")
        def update_settings(payload: SettingsUpdateRequest,
                            user: CurrentUser = Depends(self._guard)) -> dict:
            updated = self._site_settings.update(payload.values,
                                                 actor=self._actor_name(user))
            return self.ok(updated)


class AdminSystemApiController(AdminApiController):
    prefix = "/api/admin/system"

    def __init__(self, auth_handler: BaseAuthHandler, auth_service: AuthService,
                 system: SystemService, engine: SchedulerEngine | None = None) -> None:
        super().__init__(auth_handler, auth_service)
        self._system = system
        self._engine = engine

    def _register(self, router: APIRouter) -> None:
        @router.get("/health")
        def health(user: CurrentUser = Depends(self._guard)) -> dict:
            return self.ok(self._system.health_report())

        @router.get("/jobs")
        def list_jobs(user: CurrentUser = Depends(self._guard)) -> dict:
            if self._engine is None:
                return self.ok({"available": False, "jobs": []})
            return self.ok({"available": True, "jobs": self._engine.status_report})

        @router.post("/jobs/{job_name}/run")
        async def run_job(job_name: str,
                          user: CurrentUser = Depends(self._guard)) -> dict:
            if self._engine is None:
                raise ConflictError("Scheduler is not running in this process")
            try:
                result = await self._engine.run_job_now(job_name)
            except SchedulerError:
                raise NotFoundError(f"Unknown job: {job_name}") from None
            return self.ok(result.to_dict())

        @router.get("/db-connections")
        def list_profiles(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                          user: CurrentUser = Depends(self._guard)) -> dict:
            result = self._system.list_profiles(PageRequest.create(page, size))
            return self.paginated(result, lambda p: p.to_safe_dict())

        @router.post("/db-connections", status_code=201)
        def create_profile(payload: DbConnectionRequest,
                           user: CurrentUser = Depends(self._guard)) -> dict:
            profile = DbConnectionProfile(**payload.model_dump())
            created = self._system.create_profile(profile, self._actor_name(user))
            return self.ok(created.to_safe_dict())

        @router.delete("/db-connections/{profile_id}")
        def delete_profile(profile_id: int, user: CurrentUser = Depends(self._guard)) -> dict:
            self._system.delete_profile(profile_id, self._actor_name(user))
            return self.ok({"deleted": profile_id})
