"""Project templates for `pywebfw new <name>`.

Placeholders: __PROJECT__ (package name), __SECRET__ (generated key).
Kept as string constants so no package-data configuration is needed.
"""
from __future__ import annotations

PROJECT_FILES: dict[str, str] = {
    "__PROJECT__/__init__.py": '"""__PROJECT__ — application built on pywebfw."""\n',

    "__PROJECT__/main.py": '''"""ASGI entry point: `uvicorn __PROJECT__.main:app`."""
from __future__ import annotations

from pywebfw.bootstrap import ApplicationBuilder

from __PROJECT__.extensions import ProjectModule

app = ApplicationBuilder(plugins=[ProjectModule()]).build_app()
''',

    "__PROJECT__/extensions.py": '''"""Project-specific features, plugged into the framework.

Add pages, API controllers, scheduled jobs, services and event handlers here —
never modify pywebfw itself. See pywebfw.plugins.AppModule for every hook.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from pywebfw.config.settings import AppSettings
from pywebfw.core.container import ServiceContainer
from pywebfw.core.routing import BaseController
from pywebfw.domain.models import MenuArea
from pywebfw.plugins import AppModule
from pywebfw.services.menu_service import MenuService
from pywebfw.web.pages.base import PageContext, PublicPage


class WelcomePage(PublicPage):
    @property
    def title(self) -> str:
        return "Welcome"

    def build_content(self) -> str:
        return ("<h1>__PROJECT__ is running 🎉</h1>"
                "<p>Edit <code>__PROJECT__/extensions.py</code> to add your pages, "
                "APIs and jobs. The admin area is at <a href=\\"/admin\\">/admin</a> "
                "(first login: admin / ChangeMe!123 — you will be asked to change it).</p>")


class WelcomeController(BaseController):
    def __init__(self, settings: AppSettings, menus: MenuService) -> None:
        self._settings = settings
        self._menus = menus

    def _register(self, router: APIRouter) -> None:
        @router.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
        def welcome(request: Request) -> HTMLResponse:
            ctx = PageContext(
                site_name=self._settings.name,
                path=request.url.path,
                menu_items=self._menus.get_menu(MenuArea.PUBLIC),
                csp_nonce=getattr(request.state, "csp_nonce", ""),
            )
            return HTMLResponse(WelcomePage(ctx).render())


class ProjectModule(AppModule):
    name = "__PROJECT__"

    def controllers(self, container: ServiceContainer,
                    settings: AppSettings) -> list[BaseController]:
        return [WelcomeController(settings, container.resolve(MenuService))]
''',

    "tests/__init__.py": "",

    "tests/test_smoke.py": '''"""Smoke test: the application boots and serves its core surfaces."""
from __future__ import annotations

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
from pywebfw.config.settings import (
    AppSettings, CacheSettings, DatabaseSettings, MediaSettings,
    RateLimitSettings, SchedulerSettings, SecuritySettings,
)

from __PROJECT__.extensions import ProjectModule


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        name="__PROJECT__", environment="test", debug=True,
        host="127.0.0.1", port=8000,
        database=DatabaseSettings(path=str(tmp_path / "test.db"),
                                  pool_size=2, idle_timeout_seconds=60),
        security=SecuritySettings(secret_key="test-secret",
                                  token_ttl_seconds=600,
                                  password_iterations=1_000),
        cache=CacheSettings(default_ttl_seconds=60),
        scheduler=SchedulerSettings(enabled=False, tick_seconds=5),
        rate_limit=RateLimitSettings(max_requests=1_000, window_seconds=60,
                                     login_max_requests=100,
                                     login_window_seconds=60),
        media=MediaSettings(dir=str(tmp_path / "media")),
    )


def test_application_boots(tmp_path) -> None:
    app = ApplicationBuilder(_settings(tmp_path),
                             plugins=[ProjectModule()]).build_app()
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/").status_code == 200
        assert client.get("/welcome").status_code == 200
''',

    ".env": """APP_NAME=__PROJECT__
APP_ENV=development
APP_DEBUG=true
SECURITY_SECRET_KEY=__SECRET__
DB_PATH=data/app.db
""",

    ".env.example": """APP_NAME=__PROJECT__
APP_ENV=development          # development | staging | production
APP_DEBUG=true
APP_MODULES=public,admin,scheduler
SECURITY_SECRET_KEY=change-me
DB_PATH=data/app.db
# See pywebfw documentation for every setting (mail, media, redis, postgres...)
""",

    "requirements.txt": """# The framework (install from your registry, a git URL, or a local checkout):
# pip install pywebfw            (when published)
# pip install -e ../PythonWebOOP_Framework
fastapi>=0.115
uvicorn[standard]>=0.30
pydantic[email]>=2.7
python-multipart>=0.0.9
""",

    "run.py": '''"""Development entry point: `python run.py`."""
from __future__ import annotations

import uvicorn

from pywebfw.config.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run("__PROJECT__.main:app", host=settings.host,
                port=settings.port, reload=settings.debug)


if __name__ == "__main__":
    main()
''',

    "Dockerfile": """FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DB_PATH=/app/data/app.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pywebfw/ pywebfw/
COPY __PROJECT__/ __PROJECT__/
RUN useradd --create-home appuser && mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "__PROJECT__.main:app", "--host", "0.0.0.0", "--port", "8000", \\
     "--proxy-headers", "--forwarded-allow-ips", "*"]
""",

    ".gitignore": """.venv/
__pycache__/
*.pyc
.pytest_cache/
data/
.env
""",

    "README.md": """# __PROJECT__

Application built on the **pywebfw** framework.

## Run locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # plus the pywebfw package itself
python run.py
```

- Public site: http://127.0.0.1:8000/ (your page: /welcome)
- Admin: http://127.0.0.1:8000/admin (first login `admin / ChangeMe!123`,
  a password change is enforced immediately)

## Extend

Everything project-specific lives in `__PROJECT__/extensions.py` —
add pages, API controllers, scheduled jobs, services and event handlers
through `AppModule` hooks. Never modify the framework package.

## Test

```bash
pytest -q
```
""",
}


def render_project_files(project_name: str, secret_key: str) -> dict[str, str]:
    """Returns {relative_path: content} with placeholders substituted."""
    rendered: dict[str, str] = {}
    for path, content in PROJECT_FILES.items():
        rendered[path.replace("__PROJECT__", project_name)] = (
            content.replace("__PROJECT__", project_name)
                   .replace("__SECRET__", secret_key))
    return rendered
