"""Admin screens.

    AdminPage -> AdminHomePage, DashboardPage, UserManagementPage,
                 MenuManagementPage, LogManagementPage, DbConnectionManagementPage
    PublicPage -> AdminLoginPage (rendered before authentication)

Adding a new admin screen = subclass AdminPage + one route registration.
"""
from __future__ import annotations

from app.core.exceptions import NotFoundError
from app.core.pagination import PageRequest
from app.domain.models import ContentItem
from app.services.content_service import ContentService
from app.services.dashboard_service import DashboardService
from app.services.menu_service import MenuService
from app.services.system_service import SystemService
from app.services.user_service import UserService
from app.repositories.log_repository import LogRepository
from app.web.components import SeoMeta, StatCardWidget, TableComponent, esc
from app.web.pages.base import AdminPage, BasePage, PageContext
from app.web.layouts import AdminLayout, BaseLayout

_LOGIN_SCRIPT = """
<script nonce="__NONCE__">
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const message = form.querySelector('.form-message');
  const body = JSON.stringify({
    username: form.username.value,
    password: form.password.value,
  });
  const res = await fetch('/api/admin/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body,
  });
  const payload = await res.json();
  if (payload.success) {
    window.location = payload.data.user.must_change_password
      ? '/admin/change-password' : '/admin';
  }
  else { message.textContent = payload.error?.message || 'Login failed'; }
});
</script>
"""

_CHANGE_PASSWORD_SCRIPT = """
<script nonce="__NONCE__">
document.getElementById('pw-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const message = form.querySelector('.form-message');
  if (form.new_password.value !== form.confirm_password.value) {
    message.textContent = 'New passwords do not match'; return;
  }
  const res = await fetch('/api/admin/auth/change-password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify({
      current_password: form.current_password.value,
      new_password: form.new_password.value,
    }),
  });
  const payload = await res.json();
  if (payload.success) { window.location = '/admin'; }
  else { message.textContent = payload.error?.message || 'Change failed'; }
});
</script>
"""


class AdminLoginPage(BasePage):
    """Unauthenticated screen — uses the admin chrome but no admin menu."""

    @property
    def title(self) -> str:
        return "Admin Login"

    def _layout(self) -> BaseLayout:
        return AdminLayout(self._ctx.site_name, menu_items=())

    def seo(self) -> SeoMeta:
        return SeoMeta(title="Admin Login", robots="noindex, nofollow")

    def breadcrumbs(self) -> list[tuple[str, str]]:
        return []

    def build_content(self) -> str:
        script = _LOGIN_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return (
            "<h1>Sign in</h1>"
            '<form id="login-form" class="app-form">'
            '<label>Username<input name="username" required></label>'
            '<label>Password<input name="password" type="password" required></label>'
            "<button type=\"submit\">Sign in</button>"
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


class AdminPasswordChangePage(AdminPage):
    """Forced/normal password change. While `must_change_password` is set,
    every other admin screen and API redirects/blocks until this succeeds."""

    @property
    def title(self) -> str:
        return "Change Password"

    def build_content(self) -> str:
        script = _CHANGE_PASSWORD_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return (
            "<h1>Change your password</h1>"
            "<p>You must set a new password before continuing.</p>"
            '<form id="pw-form" class="app-form">'
            '<label>Current password<input name="current_password" type="password" required></label>'
            '<label>New password (min 8 chars)<input name="new_password" type="password"'
            ' minlength="8" required></label>'
            '<label>Confirm new password<input name="confirm_password" type="password"'
            ' minlength="8" required></label>'
            '<button type="submit">Update password</button>'
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


class AdminHomePage(AdminPage):
    @property
    def title(self) -> str:
        return "Admin Home"

    def build_content(self) -> str:
        links = "".join(
            f'<li><a href="{esc(item.url)}">{esc(item.title)}</a></li>'
            for item in self._ctx.menu_items
        )
        return f"<h1>Administration</h1><ul>{links}</ul>"


class DashboardPage(AdminPage):
    def __init__(self, ctx: PageContext, dashboard: DashboardService) -> None:
        super().__init__(ctx)
        self._dashboard = dashboard

    @property
    def title(self) -> str:
        return "Dashboard"

    def build_content(self) -> str:
        metrics = self._dashboard.metrics()
        cards = "".join(
            StatCardWidget(label.replace("_", " ").title(), value).render()
            for label, value in metrics["counts"].items()
        )
        db = metrics["database"]
        health = ("OK" if db.get("healthy") else "DOWN") + f' ({db.get("latency_ms", "?")} ms)'
        recent = TableComponent(
            ["Time", "Actor", "Action", "Level"],
            [(log["created_at"], log["actor"], log["action"], log["level"])
             for log in metrics["recent_logs"]],
        )
        return (f"<h1>Dashboard</h1><div>{cards}</div>"
                f"<p>Database: <strong>{esc(health)}</strong> — "
                f"Cache entries: {esc(metrics['cache']['entries'])}</p>"
                f"<h2>Recent activity</h2>{recent.render()}")


class UserManagementPage(AdminPage):
    def __init__(self, ctx: PageContext, users: UserService) -> None:
        super().__init__(ctx)
        self._users = users

    @property
    def title(self) -> str:
        return "User Management"

    def build_content(self) -> str:
        result = self._users.list_users(PageRequest.create(size=50))
        table = TableComponent(
            ["ID", "Username", "Email", "Role", "Active", "Created"],
            [(u.id, u.username, u.email, u.role.value,
              "yes" if u.is_active else "no", u.created_at) for u in result.items],
        )
        return (f"<h1>Users ({result.total})</h1>{table.render()}"
                "<p>Create/update/delete via <code>/api/admin/users</code>.</p>")


class MenuManagementPage(AdminPage):
    def __init__(self, ctx: PageContext, menus: MenuService) -> None:
        super().__init__(ctx)
        self._menus = menus

    @property
    def title(self) -> str:
        return "Menu Management"

    def build_content(self) -> str:
        result = self._menus.list_menus(PageRequest.create(size=100))
        table = TableComponent(
            ["ID", "Title", "URL", "Area", "Position", "Active"],
            [(m.id, m.title, m.url, m.area.value, m.position,
              "yes" if m.is_active else "no") for m in result.items],
        )
        return f"<h1>Menus ({result.total})</h1>{table.render()}"


class LogManagementPage(AdminPage):
    def __init__(self, ctx: PageContext, logs: LogRepository) -> None:
        super().__init__(ctx)
        self._logs = logs

    @property
    def title(self) -> str:
        return "System Logs"

    def build_content(self) -> str:
        result = self._logs.list_page(PageRequest.create(size=50))
        table = TableComponent(
            ["Time", "Actor", "Action", "Target", "Detail", "Level"],
            [(l.created_at, l.actor, l.action, l.target, l.detail, l.level)
             for l in result.items],
        )
        return f"<h1>Audit / Action Logs ({result.total})</h1>{table.render()}"


_CONTENT_FORM_SCRIPT = """
<script nonce="__NONCE__">
const form = document.getElementById('content-form');
const field = (name) => form.elements[name];
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = form.querySelector('.form-message');
  const id = form.dataset.contentId;
  const payload = {
    slug: field('slug').value, title: field('title').value,
    summary: field('summary').value, body: field('body').value,
    seo_title: field('seo_title').value, seo_description: field('seo_description').value,
    is_published: field('is_published').checked,
  };
  const res = await fetch(id ? `/api/admin/contents/${id}` : '/api/admin/contents', {
    method: id ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify(payload),
  });
  const result = await res.json();
  if (result.success) { window.location = '/admin/contents'; }
  else { message.textContent = result.error?.message || 'Save failed'; }
});
const delBtn = document.getElementById('content-delete');
if (delBtn) delBtn.addEventListener('click', async () => {
  if (!confirm('Delete this content?')) return;
  const res = await fetch(`/api/admin/contents/${delBtn.dataset.contentId}`, {
    method: 'DELETE', headers: {'X-Requested-With': 'fetch'},
  });
  if ((await res.json()).success) { window.location = '/admin/contents'; }
});
</script>
"""


class ContentManagementPage(AdminPage):
    """CMS editor: list + create/edit form, saving through the admin API."""

    def __init__(self, ctx: PageContext, contents: ContentService) -> None:
        super().__init__(ctx)
        self._contents = contents

    @property
    def title(self) -> str:
        return "Content Management"

    def build_content(self) -> str:
        parts = [self._listing()]
        edit_id = self._ctx.query.get("edit")
        if self._ctx.query.get("new") is not None:
            parts.append(self._form(None))
        elif edit_id and edit_id.isdigit():
            try:
                parts.append(self._form(self._contents.get(int(edit_id))))
            except NotFoundError:
                parts.append("<p>Content not found.</p>")
        return "".join(parts)

    def _listing(self) -> str:
        result = self._contents.list_contents(PageRequest.create(size=100))
        rows = "".join(
            f"<tr><td>{esc(item.id)}</td><td>{esc(item.slug)}</td>"
            f"<td>{esc(item.title)}</td>"
            f"<td>{'yes' if item.is_published else 'no'}</td>"
            f"<td>{esc(item.updated_at)}</td>"
            f'<td><a href="/admin/contents?edit={esc(item.id)}">Edit</a></td></tr>'
            for item in result.items
        ) or '<tr><td colspan="6">No data</td></tr>'
        return (f"<h1>Contents ({result.total})</h1>"
                '<p><a href="/admin/contents?new=1">+ New content</a></p>'
                '<table class="data-table"><thead><tr><th>ID</th><th>Slug</th>'
                "<th>Title</th><th>Published</th><th>Updated</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")

    def _form(self, item: ContentItem | None) -> str:
        value = lambda attr: esc(getattr(item, attr)) if item else ""
        checked = " checked" if (item is None or item.is_published) else ""
        id_attr = f' data-content-id="{esc(item.id)}"' if item else ""
        delete_btn = (f'<button type="button" id="content-delete"'
                      f' data-content-id="{esc(item.id)}">Delete</button>' if item else "")
        script = _CONTENT_FORM_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        heading = f"Edit: {esc(item.title)}" if item else "New content"
        return (
            f"<h2>{heading}</h2>"
            f'<form id="content-form" class="app-form"{id_attr}>'
            f'<label>Slug<input name="slug" value="{value("slug")}" required'
            ' pattern="[a-z0-9]+(-[a-z0-9]+)*"></label>'
            f'<label>Title<input name="title" value="{value("title")}" required></label>'
            f'<label>Summary<input name="summary" value="{value("summary")}"></label>'
            f'<label>Body<textarea name="body">{value("body")}</textarea></label>'
            f'<label>SEO title<input name="seo_title" value="{value("seo_title")}"></label>'
            f'<label>SEO description<input name="seo_description"'
            f' value="{value("seo_description")}"></label>'
            f'<label><input type="checkbox" name="is_published"{checked}> Published</label>'
            f"<button type=\"submit\">Save</button> {delete_btn}"
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


class DbConnectionManagementPage(AdminPage):
    def __init__(self, ctx: PageContext, system: SystemService) -> None:
        super().__init__(ctx)
        self._system = system

    @property
    def title(self) -> str:
        return "Database Connections"

    def build_content(self) -> str:
        result = self._system.list_profiles(PageRequest.create(size=50))
        table = TableComponent(
            ["ID", "Name", "Driver", "DSN", "Pool", "Idle timeout", "Default"],
            [tuple(p.to_safe_dict()[k] for k in
                   ("id", "name", "driver", "dsn", "pool_size", "idle_timeout_seconds", "is_default"))
             for p in result.items],
        )
        health = self._system.health_report()
        status = "Healthy" if health["healthy"] else "Unhealthy"
        return (f"<h1>DB Connection Profiles ({result.total})</h1>{table.render()}"
                f"<p>System health: <strong>{esc(status)}</strong></p>")
