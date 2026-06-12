"""Builds the public Community Edition tree from this private monorepo.

    python tools/export_community.py            -> dist/community/

Pipeline: copy (filtered) -> delete Pro-only files -> overlay static
community variants from editions/community/ -> programmatic surgery on the
big interwoven files (marker-based, asserts loudly on drift) -> validate
(ast.parse every touched file). The private CI should run the exported
test suite to catch drift between editions.
"""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "community"
OVERRIDES = ROOT / "editions" / "community"

COPY_EXCLUDES = {".git", ".venv", "data", "dist", "editions", "tools",
                 "__pycache__", ".pytest_cache", ".claude", ".env",
                 "pywebfw.egg-info"}

# Files that exist ONLY for Pro features -> absent from the public tree.
DELETE = [
    "pywebfw/services/traffic_service.py",
    "pywebfw/services/backup_service.py",
    "pywebfw/services/media_service.py",
    "pywebfw/services/redirect_service.py",
    "pywebfw/repositories/traffic_repository.py",
    "pywebfw/repositories/redirect_repository.py",
    "pywebfw/infrastructure/media",
    "pywebfw/core/csv_export.py",
    "tests/test_batch3.py",
    "README.md",            # replaced by the community README overlay
]


def copy_tree() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for entry in ROOT.iterdir():
        if entry.name in COPY_EXCLUDES:
            continue
        target = OUT / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target, ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", ".pytest_cache"))
        else:
            shutil.copy2(entry, target)


def delete_pro_files() -> None:
    for relative in DELETE:
        path = OUT / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()


def overlay_overrides() -> None:
    for source in OVERRIDES.rglob("*"):
        if source.is_file():
            target = OUT / source.relative_to(OVERRIDES)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _cut(text: str, start_marker: str, end_marker: str, label: str) -> str:
    """Removes [start_marker, end_marker) — both must exist exactly once."""
    assert text.count(start_marker) == 1, f"drift: start of {label}"
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[:start] + text[end:]


def _drop_line(text: str, fragment: str, label: str) -> str:
    lines = [l for l in text.splitlines(keepends=True) if fragment not in l]
    removed = len(text.splitlines()) - len(lines)
    assert removed >= 1, f"drift: line not found for {label}"
    return "".join(lines)


def surgery_admin_api() -> None:
    path = OUT / "pywebfw" / "api" / "admin_api.py"
    text = path.read_text(encoding="utf-8")

    text = text.replace(
        "from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile",
        "from fastapi import APIRouter, Depends, Query, Request, Response")
    for line in ("from fastapi.responses import FileResponse",
                 "from pywebfw.core.csv_export import CsvExporter",
                 "from pywebfw.domain.models import AuditLog, Redirect",
                 "from pywebfw.services.backup_service import BackupService",
                 "from pywebfw.services.media_service import MediaService",
                 "from pywebfw.services.redirect_service import RedirectService"):
        text = _drop_line(text, line, line)

    text = _drop_line(text, "otp: str | None = Field(None, max_length=10)",
                      "LoginRequest.otp")
    text = text.replace(
        "            result = self._auth_service.login(payload.username, payload.password,\n"
        "                                              otp=payload.otp)",
        "            result = self._auth_service.login(payload.username, payload.password)")

    text = _cut(text, "class OtpRequest(BaseModel):",
                "class ChangePasswordRequest(BaseModel):", "OtpRequest+RedirectRequest")
    text = _cut(text, '        @router.post("/2fa/setup")',
                '        @router.post("/change-password")', "2FA endpoints")
    text = _cut(text, '        # NOTE: registered before /{user_id} so "export"',
                '        @router.get("/{user_id}")', "users export")
    text = _cut(text, '        @router.post("/{user_id}/revoke-sessions")',
                "    @staticmethod", "revoke-sessions")
    text = _cut(text, '        @router.get("/export")\n        def export_messages',
                '        @router.post("/{message_id}/read")', "messages export")
    text = _cut(text, "class AdminMediaApiController(AdminApiController):",
                "class AdminLogApiController(AdminApiController):",
                "Media+Redirect+Backup controllers")
    text = _cut(text, '        @router.get("/export")\n        def export_logs',
                "class AdminDashboardApiController(AdminApiController):", "logs export")

    path.write_text(text, encoding="utf-8")


def surgery_pages_admin() -> None:
    path = OUT / "pywebfw" / "web" / "pages" / "admin.py"
    text = path.read_text(encoding="utf-8")

    for line in ("from pywebfw.services.backup_service import BackupService",
                 "from pywebfw.services.media_service import MediaService",
                 "from pywebfw.services.redirect_service import RedirectService",
                 "    BarChartComponent,"):
        text = _drop_line(text, line, line)

    # Login script/form: drop the OTP branch and field.
    text = text.replace("    otp: form.otp.value || null,\n", "")
    text = _cut(text, "  else if (payload.error?.details?.reason === 'otp_required') {",
                "  else { message.textContent = payload.error?.message || 'Login failed'; }",
                "login otp branch")
    text = _cut(text, "            '<div id=\"otp-row\" class=\"hidden\"><label>One-time code'",
                '            "<button type=\\"submit\\">Sign in</button>"', "login otp field")

    # Account page: no TOTP section/script.
    text = _cut(text, '_TOTP_SCRIPT = """', "class AdminPasswordChangePage(AdminPage):",
                "TOTP script")
    text = _cut(text, "    def _totp_section(self) -> str:",
                "    def build_content(self) -> str:", "totp section method")
    text = text.replace(
        "        nonce = esc(self._ctx.csp_nonce)\n"
        "        pw_script = _CHANGE_PASSWORD_SCRIPT.replace(\"__NONCE__\", nonce)\n"
        "        totp_script = _TOTP_SCRIPT.replace(\"__NONCE__\", nonce)\n",
        "        pw_script = _CHANGE_PASSWORD_SCRIPT.replace(\"__NONCE__\","
        " esc(self._ctx.csp_nonce))\n")
    text = text.replace(
        "            f\"{pw_script}{self._totp_section()}{totp_script}\"",
        "            f\"{pw_script}\"")

    # Dashboard: basic version without traffic widgets.
    dashboard_basic = '''    def build_content(self) -> str:
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


'''
    start = text.index('    def build_content(self) -> str:\n        metrics = self._dashboard.metrics()')
    end = text.index("_USER_FORM_SCRIPT")
    text = text[:start] + dashboard_basic + text[end:]

    # Users page: no 2FA column, no export link.
    text = _drop_line(text, "u.totp_enabled", "users 2FA cell")
    text = text.replace("<th>Active</th><th>2FA</th><th></th>",
                        "<th>Active</th><th></th>")
    text = text.replace(
        "            '<p><a href=\"/admin/users?new=1\">+ New user</a> · '\n"
        "            '<a href=\"/api/admin/users/export\">Export CSV</a></p>',",
        "            '<p><a href=\"/admin/users?new=1\">+ New user</a></p>',")

    # Logs + messages pages: no export links.
    text = _drop_line(text, '/api/admin/logs/export', "logs export link")
    text = _drop_line(text, '/api/admin/messages/export', "messages export link")

    # Remove the contiguous Media/Session/Backup/Redirect page blocks
    # (they all sit between the media script and DbConnection page).
    text = _cut(text, '_MEDIA_UPLOAD_SCRIPT = """',
                "class DbConnectionManagementPage(AdminPage):",
                "media/session/backup/redirect pages")

    path.write_text(text, encoding="utf-8")


def surgery_controllers() -> None:
    path = OUT / "pywebfw" / "web" / "controllers.py"
    text = path.read_text(encoding="utf-8")

    for line in ("from pywebfw.services.backup_service import BackupService",
                 "from pywebfw.services.media_service import MediaService",
                 "from pywebfw.services.redirect_service import RedirectService",
                 "    BackupManagerPage,",
                 "    MediaManagerPage,",
                 "    SessionManagerPage,",
                 "    RedirectManagementPage,",
                 "    media: MediaService",
                 "    backups: BackupService",
                 "    redirects: RedirectService"):
        text = _drop_line(text, line, line)
    text = text.replace(
        "from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response",
        "from fastapi.responses import HTMLResponse, RedirectResponse, Response")

    text = _cut(text, "class MediaWebController(BaseController):",
                "class AdminWebController(BaseController):", "MediaWebController")
    for line in ('"/media": lambda ctx: MediaManagerPage(ctx, deps.media),',
                 '"/sessions": lambda ctx: SessionManagerPage(ctx, deps.users, deps.logs),',
                 '"/redirects": lambda ctx: RedirectManagementPage(ctx, deps.redirects),',
                 '"/backups": lambda ctx: BackupManagerPage(ctx, deps.backups),'):
        text = _drop_line(text, line, line)

    path.write_text(text, encoding="utf-8")


def surgery_tests() -> None:
    # test_batch1: drop the two traffic tests; jobs assertion uses a core job.
    path = OUT / "tests" / "test_batch1.py"
    text = path.read_text(encoding="utf-8")
    text = _cut(text, "def test_traffic_is_counted_and_reported(",
                "def test_robots_txt(", "traffic tests")
    text = text.replace('assert "traffic-flush" in job_names',
                        'assert "database-health-check" in job_names')
    path.write_text(text, encoding="utf-8")

    # test_batch2: keep contact tests only.
    path = OUT / "tests" / "test_batch2.py"
    text = path.read_text(encoding="utf-8")
    text = _cut(text, "# --- media manager ", '"""Batch2-end"""', "pro batch2 tests") \
        if '"""Batch2-end"""' in text else text[:text.index("# --- media manager ")]
    path.write_text(text, encoding="utf-8")

    # test_content_admin: drop the backup job test.
    path = OUT / "tests" / "test_content_admin.py"
    text = path.read_text(encoding="utf-8")
    text = text[:text.index("def test_database_backup_job(")].rstrip() + "\n"
    text = _drop_line(text, "from pywebfw.scheduler.base import JobStatus", "JobStatus import")
    text = _drop_line(text, "import asyncio", "asyncio import")
    path.write_text(text, encoding="utf-8")


def validate() -> None:
    failures = []
    for py in (OUT / "pywebfw").rglob("*.py"):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{py}: {exc}")
    for py in (OUT / "tests").rglob("*.py"):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{py}: {exc}")
    # No file in the public tree may import a Pro-only module.
    banned = ("traffic_service", "backup_service", "media_service",
              "redirect_service", "csv_export", "TotpProvider",
              "PostgresDatabaseManager", "RedisCacheManager")
    for py in OUT.rglob("*.py"):
        content = py.read_text(encoding="utf-8")
        for term in banned:
            if f"import {term}" in content or f"from pywebfw.services.{term}" in content:
                failures.append(f"{py}: references Pro module '{term}'")
    if failures:
        print("EXPORT FAILED:")
        for failure in failures:
            print(" -", failure)
        sys.exit(1)
    print(f"Community edition exported to {OUT}")


def main() -> None:
    copy_tree()
    delete_pro_files()
    overlay_overrides()
    surgery_admin_api()
    surgery_pages_admin()
    surgery_controllers()
    surgery_tests()
    validate()


if __name__ == "__main__":
    main()
