"""Admin screens.

    AdminPage -> AdminHomePage, DashboardPage, UserManagementPage,
                 MenuManagementPage, LogManagementPage, DbConnectionManagementPage
    PublicPage -> AdminLoginPage (rendered before authentication)

Adding a new admin screen = subclass AdminPage + one route registration.
"""
from __future__ import annotations

from pywebfw.core.exceptions import NotFoundError
from pywebfw.core.pagination import PageRequest
from pywebfw.domain.models import ContentItem
from pywebfw.scheduler.engine import SchedulerEngine
from pywebfw.services.backup_service import BackupService
from pywebfw.services.contact_service import ContactService
from pywebfw.services.content_service import ContentService
from pywebfw.services.dashboard_service import DashboardService
from pywebfw.services.media_service import MediaService
from pywebfw.services.menu_service import MenuService
from pywebfw.services.redirect_service import RedirectService
from pywebfw.services.site_settings_service import KNOWN_SETTINGS, SiteSettingsService
from pywebfw.services.system_service import SystemService
from pywebfw.services.user_service import UserService
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.web.components import (
    BarChartComponent,
    PaginationComponent,
    SeoMeta,
    StatCardWidget,
    TableComponent,
    esc,
)
from pywebfw.web.pages.base import AdminPage, BasePage, PageContext
from pywebfw.web.layouts import AdminLayout, BaseLayout

_LOGIN_SCRIPT = """
<script nonce="__NONCE__">
document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const form = e.target;
  const message = form.querySelector('.form-message');
  const body = JSON.stringify({
    username: form.username.value,
    password: form.password.value,
    otp: form.otp.value || null,
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
  else if (payload.error?.details?.reason === 'otp_required') {
    document.getElementById('otp-row').classList.remove('hidden');
    message.textContent = 'Enter the code from your authenticator app.';
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
            '<div id="otp-row" class="hidden"><label>One-time code'
            '<input name="otp" inputmode="numeric" autocomplete="one-time-code"'
            ' maxlength="10"></label></div>'
            "<button type=\"submit\">Sign in</button>"
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


_TOTP_SCRIPT = """
<script nonce="__NONCE__">
const tfa = document.getElementById('tfa');
const tfaMessage = tfa.querySelector('.form-message');
const post = async (url, body) => {
  const res = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: body ? JSON.stringify(body) : null,
  });
  return res.json();
};
const setupBtn = document.getElementById('tfa-setup');
if (setupBtn) setupBtn.addEventListener('click', async () => {
  const result = await post('/api/admin/auth/2fa/setup');
  if (!result.success) { tfaMessage.textContent = result.error?.message; return; }
  document.getElementById('tfa-secret').textContent = result.data.secret;
  document.getElementById('tfa-uri').textContent = result.data.otpauth_uri;
  document.getElementById('tfa-confirm').classList.remove('hidden');
});
const enableBtn = document.getElementById('tfa-enable');
if (enableBtn) enableBtn.addEventListener('click', async () => {
  const result = await post('/api/admin/auth/2fa/enable',
    {otp: document.getElementById('tfa-otp').value});
  if (result.success) { window.location.reload(); }
  else { tfaMessage.textContent = result.error?.message || 'Invalid code'; }
});
const disableBtn = document.getElementById('tfa-disable');
if (disableBtn) disableBtn.addEventListener('click', async () => {
  const result = await post('/api/admin/auth/2fa/disable',
    {otp: document.getElementById('tfa-otp-off').value});
  if (result.success) { window.location.reload(); }
  else { tfaMessage.textContent = result.error?.message || 'Invalid code'; }
});
</script>
"""


class AdminPasswordChangePage(AdminPage):
    """Account security screen: password change + two-factor authentication.
    While `must_change_password` is set, every other admin screen and API
    redirects/blocks until the password is changed here."""

    @property
    def title(self) -> str:
        return "Account Security"

    def _totp_section(self) -> str:
        enabled = bool(self._ctx.user and self._ctx.user.totp_enabled)
        if enabled:
            body = (
                "<p>Status: <strong>enabled</strong> ✅</p>"
                '<label>One-time code<input id="tfa-otp-off" inputmode="numeric"'
                ' maxlength="10"></label>'
                '<button type="button" id="tfa-disable">Disable 2FA</button>'
            )
        else:
            body = (
                "<p>Status: <strong>disabled</strong> — protect this account with "
                "an authenticator app (Google Authenticator, Authy, 1Password...).</p>"
                '<button type="button" id="tfa-setup">Set up 2FA</button>'
                '<div id="tfa-confirm" class="hidden">'
                "<p>1. Add this secret to your authenticator app:</p>"
                '<p><code id="tfa-secret"></code></p>'
                '<p><small>URI: <code id="tfa-uri"></code></small></p>'
                "<p>2. Enter the generated code to confirm:</p>"
                '<label>One-time code<input id="tfa-otp" inputmode="numeric"'
                ' maxlength="10"></label>'
                '<button type="button" id="tfa-enable">Enable 2FA</button></div>'
            )
        return (f'<div id="tfa" class="app-form"><h2>Two-factor authentication</h2>'
                f'{body}<div class="form-message" role="alert"></div></div>')

    def build_content(self) -> str:
        nonce = esc(self._ctx.csp_nonce)
        pw_script = _CHANGE_PASSWORD_SCRIPT.replace("__NONCE__", nonce)
        totp_script = _TOTP_SCRIPT.replace("__NONCE__", nonce)
        return (
            "<h1>Account security</h1>"
            "<h2>Change password</h2>"
            '<form id="pw-form" class="app-form">'
            '<label>Current password<input name="current_password" type="password" required></label>'
            '<label>New password (min 8 chars)<input name="new_password" type="password"'
            ' minlength="8" required></label>'
            '<label>Confirm new password<input name="confirm_password" type="password"'
            ' minlength="8" required></label>'
            '<button type="submit">Update password</button>'
            '<div class="form-message" role="alert"></div></form>'
            f"{pw_script}{self._totp_section()}{totp_script}"
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
        traffic = metrics["traffic"]
        cards = StatCardWidget("Online Now", traffic["online"]).render()
        cards += StatCardWidget("Views Today", traffic["today_hits"]).render()
        cards += "".join(
            StatCardWidget(label.replace("_", " ").title(), value).render()
            for label, value in metrics["counts"].items()
        )
        chart = BarChartComponent(
            [(row["day"][5:], row["hits"]) for row in traffic["series"]],
            title="Page views — last 7 days",
        )
        top_pages = TableComponent(
            ["Path", "Hits (7d)"],
            [(row["path"], row["hits"]) for row in traffic["top_pages"]],
        )
        db = metrics["database"]
        health = ("OK" if db.get("healthy") else "DOWN") + f' ({db.get("latency_ms", "?")} ms)'
        recent = TableComponent(
            ["Time", "Actor", "Action", "Level"],
            [(log["created_at"], log["actor"], log["action"], log["level"])
             for log in metrics["recent_logs"]],
        )
        return (f"<h1>Dashboard</h1><div>{cards}</div>"
                f"{chart.render()}"
                f"<h2>Top pages</h2>{top_pages.render()}"
                f"<p>Database: <strong>{esc(health)}</strong> — "
                f"Cache entries: {esc(metrics['cache']['entries'])}</p>"
                f"<h2>Recent activity</h2>{recent.render()}")


_USER_FORM_SCRIPT = """
<script nonce="__NONCE__">
const uForm = document.getElementById('user-form');
const uField = (name) => uForm.elements[name];
uForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = uForm.querySelector('.form-message');
  const id = uForm.dataset.userId;
  const payload = {
    username: uField('username').value, email: uField('email').value,
    role: uField('role').value, is_active: uField('is_active').checked,
  };
  if (uField('password').value) { payload.password = uField('password').value; }
  const res = await fetch(id ? `/api/admin/users/${id}` : '/api/admin/users', {
    method: id ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify(payload),
  });
  const result = await res.json();
  if (result.success) { window.location = '/admin/users'; }
  else { message.textContent = result.error?.message || 'Save failed'; }
});
const uDelete = document.getElementById('user-delete');
if (uDelete) uDelete.addEventListener('click', async () => {
  if (!confirm('Delete this user?')) return;
  const res = await fetch(`/api/admin/users/${uDelete.dataset.userId}`, {
    method: 'DELETE', headers: {'X-Requested-With': 'fetch'},
  });
  const result = await res.json();
  if (result.success) { window.location = '/admin/users'; }
  else { uForm.querySelector('.form-message').textContent = result.error?.message; }
});
</script>
"""

_ROLES = ("admin", "editor", "viewer")


class UserManagementPage(AdminPage):
    PAGE_SIZE = 20

    def __init__(self, ctx: PageContext, users: UserService) -> None:
        super().__init__(ctx)
        self._users = users

    @property
    def title(self) -> str:
        return "User Management"

    def build_content(self) -> str:
        page = self.query_int("page")
        result = self._users.list_users(PageRequest.create(page=page, size=self.PAGE_SIZE))
        rows = "".join(
            f"<tr><td>{esc(u.id)}</td><td>{esc(u.username)}</td><td>{esc(u.email)}</td>"
            f"<td>{esc(u.role.value)}</td><td>{'yes' if u.is_active else 'no'}</td>"
            f"<td>{'on' if u.totp_enabled else 'off'}</td>"
            f'<td><a href="/admin/users?edit={esc(u.id)}">Edit</a></td></tr>'
            for u in result.items
        )
        parts = [
            f"<h1>Users ({result.total})</h1>",
            '<p><a href="/admin/users?new=1">+ New user</a> · '
            '<a href="/api/admin/users/export">Export CSV</a></p>',
            '<table class="data-table"><thead><tr><th>ID</th><th>Username</th>'
            "<th>Email</th><th>Role</th><th>Active</th><th>2FA</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table>",
            PaginationComponent(result.page, result.pages, "/admin/users").render(),
        ]
        edit_id = self._ctx.query.get("edit", "")
        if self._ctx.query.get("new") is not None:
            parts.append(self._form(None))
        elif edit_id.isdigit():
            try:
                parts.append(self._form(self._users.get(int(edit_id))))
            except NotFoundError:
                parts.append("<p>User not found.</p>")
        return "".join(parts)

    def _form(self, user) -> str:
        value = lambda attr: esc(getattr(user, attr)) if user else ""
        role_value = user.role.value if user else "viewer"
        options = "".join(
            f'<option value="{r}"{" selected" if r == role_value else ""}>{r}</option>'
            for r in _ROLES)
        checked = " checked" if (user is None or user.is_active) else ""
        id_attr = f' data-user-id="{esc(user.id)}"' if user else ""
        delete_btn = (f'<button type="button" id="user-delete"'
                      f' data-user-id="{esc(user.id)}">Delete</button>' if user else "")
        pw_label = ("Password (leave blank to keep current)" if user
                    else "Password (min 8 chars)")
        pw_required = "" if user else " required"
        heading = f"Edit: {esc(user.username)}" if user else "New user"
        script = _USER_FORM_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return (
            f"<h2>{heading}</h2>"
            f'<form id="user-form" class="app-form"{id_attr}>'
            f'<label>Username<input name="username" value="{value("username")}"'
            ' required minlength="3"></label>'
            f'<label>Email<input name="email" type="email" value="{value("email")}" required></label>'
            f'<label>{pw_label}<input name="password" type="password" minlength="8"{pw_required}></label>'
            f'<label>Role<select name="role">{options}</select></label>'
            f'<label><input type="checkbox" name="is_active"{checked}> Active</label>'
            f"<button type=\"submit\">Save</button> {delete_btn}"
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


_MENU_FORM_SCRIPT = """
<script nonce="__NONCE__">
const mForm = document.getElementById('menu-form');
const mField = (name) => mForm.elements[name];
mForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = mForm.querySelector('.form-message');
  const id = mForm.dataset.menuId;
  const payload = {
    title: mField('title').value, url: mField('url').value,
    area: mField('area').value,
    position: parseInt(mField('position').value, 10) || 0,
    is_active: mField('is_active').checked,
  };
  const res = await fetch(id ? `/api/admin/menus/${id}` : '/api/admin/menus', {
    method: id ? 'PUT' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify(payload),
  });
  const result = await res.json();
  if (result.success) { window.location = '/admin/menus'; }
  else { message.textContent = result.error?.message || 'Save failed'; }
});
const mDelete = document.getElementById('menu-delete');
if (mDelete) mDelete.addEventListener('click', async () => {
  if (!confirm('Delete this menu item?')) return;
  const res = await fetch(`/api/admin/menus/${mDelete.dataset.menuId}`, {
    method: 'DELETE', headers: {'X-Requested-With': 'fetch'},
  });
  if ((await res.json()).success) { window.location = '/admin/menus'; }
});
</script>
"""


class MenuManagementPage(AdminPage):
    PAGE_SIZE = 50

    def __init__(self, ctx: PageContext, menus: MenuService) -> None:
        super().__init__(ctx)
        self._menus = menus

    @property
    def title(self) -> str:
        return "Menu Management"

    def build_content(self) -> str:
        page = self.query_int("page")
        result = self._menus.list_menus(PageRequest.create(page=page, size=self.PAGE_SIZE))
        rows = "".join(
            f"<tr><td>{esc(m.id)}</td><td>{esc(m.title)}</td><td>{esc(m.url)}</td>"
            f"<td>{esc(m.area.value)}</td><td>{esc(m.position)}</td>"
            f"<td>{'yes' if m.is_active else 'no'}</td>"
            f'<td><a href="/admin/menus?edit={esc(m.id)}">Edit</a></td></tr>'
            for m in result.items
        )
        parts = [
            f"<h1>Menus ({result.total})</h1>",
            '<p><a href="/admin/menus?new=1">+ New menu item</a></p>',
            '<table class="data-table"><thead><tr><th>ID</th><th>Title</th>'
            "<th>URL</th><th>Area</th><th>Position</th><th>Active</th><th></th>"
            f"</tr></thead><tbody>{rows}</tbody></table>",
            PaginationComponent(result.page, result.pages, "/admin/menus").render(),
        ]
        edit_id = self._ctx.query.get("edit", "")
        if self._ctx.query.get("new") is not None:
            parts.append(self._form(None))
        elif edit_id.isdigit():
            try:
                parts.append(self._form(self._menus.get(int(edit_id))))
            except NotFoundError:
                parts.append("<p>Menu item not found.</p>")
        return "".join(parts)

    def _form(self, item) -> str:
        value = lambda attr: esc(getattr(item, attr)) if item else ""
        area_value = item.area.value if item else "public"
        options = "".join(
            f'<option value="{a}"{" selected" if a == area_value else ""}>{a}</option>'
            for a in ("public", "admin"))
        checked = " checked" if (item is None or item.is_active) else ""
        id_attr = f' data-menu-id="{esc(item.id)}"' if item else ""
        delete_btn = (f'<button type="button" id="menu-delete"'
                      f' data-menu-id="{esc(item.id)}">Delete</button>' if item else "")
        position = esc(item.position) if item else "0"
        heading = f"Edit: {esc(item.title)}" if item else "New menu item"
        script = _MENU_FORM_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return (
            f"<h2>{heading}</h2>"
            f'<form id="menu-form" class="app-form"{id_attr}>'
            f'<label>Title<input name="title" value="{value("title")}" required></label>'
            f'<label>URL<input name="url" value="{value("url")}" required></label>'
            f'<label>Area<select name="area">{options}</select></label>'
            f'<label>Position<input name="position" type="number" value="{position}"></label>'
            f'<label><input type="checkbox" name="is_active"{checked}> Active</label>'
            f"<button type=\"submit\">Save</button> {delete_btn}"
            '<div class="form-message" role="alert"></div></form>'
            f"{script}"
        )


class LogManagementPage(AdminPage):
    def __init__(self, ctx: PageContext, logs: LogRepository) -> None:
        super().__init__(ctx)
        self._logs = logs

    @property
    def title(self) -> str:
        return "System Logs"

    def build_content(self) -> str:
        page = self.query_int("page")
        result = self._logs.list_page(PageRequest.create(page=page, size=50))
        table = TableComponent(
            ["Time", "Actor", "Action", "Target", "Detail", "Level"],
            [(l.created_at, l.actor, l.action, l.target, l.detail, l.level)
             for l in result.items],
        )
        return (f"<h1>Audit / Action Logs ({result.total})</h1>"
                '<p><a href="/api/admin/logs/export">Export CSV</a></p>'
                f"{table.render()}"
                f"{PaginationComponent(result.page, result.pages, '/admin/logs').render()}")


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
        page = self.query_int("page")
        result = self._contents.list_contents(PageRequest.create(page=page, size=50))
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
                f"<tbody>{rows}</tbody></table>"
                f"{PaginationComponent(result.page, result.pages, '/admin/contents').render()}")

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


_JOBS_SCRIPT = """
<script nonce="__NONCE__">
document.querySelectorAll('button[data-job]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    btn.disabled = true; btn.textContent = 'Running...';
    await fetch(`/api/admin/system/jobs/${btn.dataset.job}/run`, {
      method: 'POST', headers: {'X-Requested-With': 'fetch'},
    });
    window.location.reload();
  });
});
</script>
"""


class JobsMonitorPage(AdminPage):
    """Scheduler observability: every job, its schedule, last result, and a
    manual trigger. Shows a notice when this process has no scheduler module."""

    def __init__(self, ctx: PageContext, engine: SchedulerEngine | None) -> None:
        super().__init__(ctx)
        self._engine = engine

    @property
    def title(self) -> str:
        return "Scheduled Jobs"

    def build_content(self) -> str:
        if self._engine is None:
            return ("<h1>Scheduled Jobs</h1><p>The scheduler module is not "
                    "running in this process (see <code>APP_MODULES</code>). "
                    "Job results are visible in the audit log.</p>")
        rows = []
        for entry in self._engine.status_report:
            last = entry["last_result"]
            if last:
                status = last["status"]
                detail = (f'{last["duration_ms"]} ms · attempts {last["attempts"]} · '
                          f'{last["message"] or last["error"]}')
                started = last["started_at"]
            else:
                status, detail, started = "—", "not run yet", "—"
            rows.append(
                f"<tr><td>{esc(entry['job'])}</td><td>{esc(entry['schedule'])}</td>"
                f"<td>{esc(status)}</td><td>{esc(started)}</td><td>{esc(detail)}</td>"
                f'<td><button data-job="{esc(entry["job"])}">Run now</button></td></tr>'
            )
        script = _JOBS_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return ("<h1>Scheduled Jobs</h1>"
                '<table class="data-table"><thead><tr><th>Job</th><th>Schedule</th>'
                "<th>Last status</th><th>Started</th><th>Detail</th><th></th></tr></thead>"
                f'<tbody>{"".join(rows)}</tbody></table>{script}')


_SETTINGS_SCRIPT = """
<script nonce="__NONCE__">
const form = document.getElementById('settings-form');
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = form.querySelector('.form-message');
  const values = {};
  form.querySelectorAll('[data-setting]').forEach((el) => {
    values[el.dataset.setting] = el.type === 'checkbox'
      ? (el.checked ? '1' : '0') : el.value;
  });
  const res = await fetch('/api/admin/settings', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify({values}),
  });
  const result = await res.json();
  message.textContent = result.success
    ? 'Saved.' : (result.error?.message || 'Save failed');
});
</script>
"""


class SettingsPage(AdminPage):
    """Runtime system configuration — values apply without redeploying."""

    def __init__(self, ctx: PageContext, site_settings: SiteSettingsService) -> None:
        super().__init__(ctx)
        self._site_settings = site_settings

    @property
    def title(self) -> str:
        return "System Settings"

    def build_content(self) -> str:
        values = self._site_settings.all()
        fields = []
        for key in KNOWN_SETTINGS:
            label = key.replace("_", " ").title()
            if key == "maintenance_mode":
                checked = " checked" if values[key] == "1" else ""
                fields.append(
                    f'<label><input type="checkbox" data-setting="{esc(key)}"{checked}>'
                    f" {label} <small>(public site answers 503 while enabled)</small></label>")
            else:
                fields.append(
                    f'<label>{label}<input data-setting="{esc(key)}"'
                    f' value="{esc(values[key])}"></label>')
        script = _SETTINGS_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return ("<h1>System Settings</h1>"
                '<form id="settings-form" class="app-form">'
                + "".join(fields) +
                '<button type="submit">Save settings</button>'
                '<div class="form-message" role="alert"></div></form>'
                f"{script}")


_ACTION_BUTTONS_SCRIPT = """
<script nonce="__NONCE__">
document.querySelectorAll('button[data-action]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    if (btn.dataset.confirm && !confirm(btn.dataset.confirm)) return;
    btn.disabled = true;
    await fetch(btn.dataset.action, {
      method: btn.dataset.method || 'POST',
      headers: {'X-Requested-With': 'fetch'},
    });
    window.location.reload();
  });
});
</script>
"""


def _action_script(nonce: str) -> str:
    return _ACTION_BUTTONS_SCRIPT.replace("__NONCE__", esc(nonce))


class ContactMessagesPage(AdminPage):
    """Inbox for public contact-form submissions."""

    def __init__(self, ctx: PageContext, contact: ContactService) -> None:
        super().__init__(ctx)
        self._contact = contact

    @property
    def title(self) -> str:
        return "Contact Messages"

    def build_content(self) -> str:
        page = self.query_int("page")
        result = self._contact.list_messages(PageRequest.create(page=page, size=50))
        rows = "".join(
            f"<tr><td>{esc(m.created_at)}</td><td>{esc(m.name)}</td>"
            f"<td>{esc(m.email)}</td><td>{esc(m.subject)}</td>"
            f"<td>{esc(m.message[:120])}</td>"
            f"<td>{'✓' if m.is_read else '<strong>new</strong>'}</td>"
            f'<td><button data-action="/api/admin/messages/{esc(m.id)}/read">Read</button> '
            f'<button data-action="/api/admin/messages/{esc(m.id)}" data-method="DELETE"'
            f' data-confirm="Delete this message?">Delete</button></td></tr>'
            for m in result.items
        ) or '<tr><td colspan="7">No messages</td></tr>'
        return (f"<h1>Messages ({result.total} — {self._contact.unread_count()} unread)</h1>"
                '<p><a href="/api/admin/messages/export">Export CSV</a></p>'
                '<table class="data-table"><thead><tr><th>Date</th><th>Name</th>'
                "<th>Email</th><th>Subject</th><th>Message</th><th>Status</th><th></th>"
                f"</tr></thead><tbody>{rows}</tbody></table>"
                f"{PaginationComponent(result.page, result.pages, '/admin/messages').render()}"
                f"{_action_script(self._ctx.csp_nonce)}")


_MEDIA_UPLOAD_SCRIPT = """
<script nonce="__NONCE__">
const uploadForm = document.getElementById('upload-form');
uploadForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = uploadForm.querySelector('.form-message');
  const fileInput = uploadForm.elements['file'];
  if (!fileInput.files.length) { message.textContent = 'Choose a file first.'; return; }
  const body = new FormData();
  body.append('file', fileInput.files[0]);
  const res = await fetch('/api/admin/media', {
    method: 'POST', headers: {'X-Requested-With': 'fetch'}, body,
  });
  const result = await res.json();
  if (result.success) { window.location.reload(); }
  else { message.textContent = result.error?.message || 'Upload failed'; }
});
</script>
"""


class MediaManagerPage(AdminPage):
    """Upload and manage media files served under /media/."""

    def __init__(self, ctx: PageContext, media: MediaService) -> None:
        super().__init__(ctx)
        self._media = media

    @property
    def title(self) -> str:
        return "Media Manager"

    def build_content(self) -> str:
        files = self._media.list_files()
        rows = "".join(
            f'<tr><td><a href="{esc(f["url"])}" target="_blank">{esc(f["name"])}</a></td>'
            f"<td>{f['size_bytes'] // 1024} KB</td><td>{esc(f['modified_at'])}</td>"
            f"<td><code>{esc(f['url'])}</code></td>"
            f'<td><button data-action="/api/admin/media/{esc(f["name"])}"'
            f' data-method="DELETE" data-confirm="Delete this file?">Delete</button></td></tr>'
            for f in files
        ) or '<tr><td colspan="5">No files uploaded yet</td></tr>'
        upload_script = _MEDIA_UPLOAD_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        return (f"<h1>Media ({len(files)})</h1>"
                '<form id="upload-form" class="app-form">'
                '<label>Upload file (jpg, png, gif, webp, pdf)'
                '<input type="file" name="file" required></label>'
                '<button type="submit">Upload</button>'
                '<div class="form-message" role="alert"></div></form>'
                '<table class="data-table"><thead><tr><th>File</th><th>Size</th>'
                "<th>Uploaded</th><th>URL</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"{upload_script}{_action_script(self._ctx.csp_nonce)}")


class SessionManagerPage(AdminPage):
    """Active-session overview: last login per user + revoke-everywhere."""

    def __init__(self, ctx: PageContext, users: UserService,
                 logs: LogRepository) -> None:
        super().__init__(ctx)
        self._users = users
        self._logs = logs

    @property
    def title(self) -> str:
        return "Session Manager"

    def build_content(self) -> str:
        result = self._users.list_users(PageRequest.create(size=100))
        last_logins = self._logs.last_login_map()
        rows = "".join(
            f"<tr><td>{esc(u.username)}</td><td>{esc(u.role.value)}</td>"
            f"<td>{'yes' if u.is_active else 'no'}</td>"
            f"<td>{esc(last_logins.get(u.username, 'never'))}</td>"
            f'<td><button data-action="/api/admin/users/{esc(u.id)}/revoke-sessions"'
            f' data-confirm="Revoke all sessions of {esc(u.username)}?">'
            f"Revoke sessions</button></td></tr>"
            for u in result.items
        )
        return (f"<h1>Sessions ({result.total} users)</h1>"
                "<p>Revoking invalidates every token of that user immediately "
                "(they must sign in again on all devices).</p>"
                '<table class="data-table"><thead><tr><th>User</th><th>Role</th>'
                "<th>Active</th><th>Last login</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"{_action_script(self._ctx.csp_nonce)}")


class BackupManagerPage(AdminPage):
    """List, create, download and delete database backups."""

    def __init__(self, ctx: PageContext, backups: BackupService) -> None:
        super().__init__(ctx)
        self._backups = backups

    @property
    def title(self) -> str:
        return "Backups"

    def build_content(self) -> str:
        if not self._backups.supported:
            return ("<h1>Backups</h1><p>Online backup is only available for "
                    "file-based SQLite. Use pg_dump / managed backups for "
                    "this database engine.</p>")
        backups = self._backups.list_backups()
        rows = "".join(
            f"<tr><td>{esc(b['name'])}</td><td>{b['size_bytes'] // 1024} KB</td>"
            f"<td>{esc(b['created_at'])}</td>"
            f'<td><a href="/api/admin/backups/{esc(b["name"])}/download">Download</a> '
            f'<button data-action="/api/admin/backups/{esc(b["name"])}"'
            f' data-method="DELETE" data-confirm="Delete this backup?">Delete</button>'
            f"</td></tr>"
            for b in backups
        ) or '<tr><td colspan="4">No backups yet</td></tr>'
        return (f"<h1>Backups ({len(backups)})</h1>"
                '<p><button data-action="/api/admin/backups">Create backup now</button>'
                " — automatic nightly at 02:30, newest 7 kept.</p>"
                '<table class="data-table"><thead><tr><th>File</th><th>Size</th>'
                "<th>Created</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"{_action_script(self._ctx.csp_nonce)}")


_REDIRECT_FORM_SCRIPT = """
<script nonce="__NONCE__">
const rForm = document.getElementById('redirect-form');
rForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = rForm.querySelector('.form-message');
  const res = await fetch('/api/admin/redirects', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'fetch'},
    body: JSON.stringify({
      from_path: rForm.elements['from_path'].value,
      to_path: rForm.elements['to_path'].value,
      status_code: parseInt(rForm.elements['status_code'].value, 10),
    }),
  });
  const result = await res.json();
  if (result.success) { window.location.reload(); }
  else { message.textContent = result.error?.message || 'Save failed'; }
});
</script>
"""


class RedirectManagementPage(AdminPage):
    """301/302 rules — slug renames create them automatically (event bus)."""

    def __init__(self, ctx: PageContext, redirects: "RedirectService") -> None:
        super().__init__(ctx)
        self._redirects = redirects

    @property
    def title(self) -> str:
        return "Redirects"

    def build_content(self) -> str:
        page = self.query_int("page")
        result = self._redirects.list_redirects(PageRequest.create(page=page, size=50))
        rows = "".join(
            f"<tr><td>{esc(r.from_path)}</td><td>{esc(r.to_path)}</td>"
            f"<td>{esc(r.status_code)}</td><td>{esc(r.hits)}</td>"
            f"<td>{'yes' if r.is_active else 'no'}</td>"
            f'<td><button data-action="/api/admin/redirects/{esc(r.id)}"'
            f' data-method="DELETE" data-confirm="Delete this redirect?">Delete</button>'
            f"</td></tr>"
            for r in result.items
        ) or '<tr><td colspan="6">No redirects</td></tr>'
        nonce = esc(self._ctx.csp_nonce)
        form_script = _REDIRECT_FORM_SCRIPT.replace("__NONCE__", nonce)
        return (f"<h1>Redirects ({result.total})</h1>"
                "<p>Renaming a content slug creates a 301 automatically.</p>"
                '<form id="redirect-form" class="app-form">'
                '<label>From path<input name="from_path" required placeholder="/old-page"></label>'
                '<label>To path<input name="to_path" required placeholder="/new-page"></label>'
                '<label>Status<input name="status_code" value="301" required'
                ' pattern="30[12]"></label>'
                '<button type="submit">Add redirect</button>'
                '<div class="form-message" role="alert"></div></form>'
                '<table class="data-table"><thead><tr><th>From</th><th>To</th>'
                "<th>Code</th><th>Hits</th><th>Active</th><th></th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"{PaginationComponent(result.page, result.pages, '/admin/redirects').render()}"
                f"{form_script}{_action_script(self._ctx.csp_nonce)}")


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
