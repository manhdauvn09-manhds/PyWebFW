"""Path B: pywebfw as a reusable package — plugin hooks, demo app, CLI scaffold."""
from __future__ import annotations

import py_compile

from fastapi.testclient import TestClient

from pywebfw.bootstrap import ApplicationBuilder
from pywebfw.cli import main as cli_main
from pywebfw.core.events import EventBus
from pywebfw.plugins import AppModule
from pywebfw.scheduler.base import BaseSchedulerJob, IntervalSchedule
from tests.conftest import build_test_settings


class PingJob(BaseSchedulerJob):
    name = "plugin-ping-job"
    schedule = IntervalSchedule(999_999)

    def run(self) -> str:
        return "pong"


class SamplePlugin(AppModule):
    name = "sample"

    def __init__(self) -> None:
        self.events_wired = False
        self.schema_ran = False

    def controllers(self, container, settings):
        from fastapi import APIRouter

        from pywebfw.core.routing import BaseApiController

        class PingController(BaseApiController):
            prefix = "/api/plugin"

            def _register(self, router: APIRouter) -> None:
                @router.get("/ping")
                def ping() -> dict:
                    return self.ok({"pong": True})

        return [PingController()]

    def jobs(self, container):
        return [PingJob()]

    def subscribe_events(self, bus: EventBus, container) -> None:
        self.events_wired = True

    def init_schema(self, db) -> None:
        self.schema_ran = True
        db.execute("CREATE TABLE IF NOT EXISTS plugin_notes ("
                   "id INTEGER PRIMARY KEY AUTOINCREMENT, note TEXT)")


def test_plugin_hooks_are_invoked(tmp_path) -> None:
    plugin = SamplePlugin()
    settings = build_test_settings(str(tmp_path / "plugin.db"))
    app = ApplicationBuilder(settings, plugins=[plugin]).build_app()
    with TestClient(app) as client:
        # Controller mounted with the standard envelope.
        response = client.get("/api/plugin/ping")
        assert response.status_code == 200
        assert response.json()["data"]["pong"] is True
        # Job registered alongside the built-ins.
        jobs = [entry["job"] for entry in client.get("/healthz").json()["scheduler"]]
        assert "plugin-ping-job" in jobs
        # Schema + event hooks ran.
        assert plugin.schema_ran and plugin.events_wired
        # Built-in surfaces are untouched.
        assert client.get("/").status_code == 200


def test_demo_app_module(tmp_path) -> None:
    """The demo application's plugin works end-to-end."""
    from app.extensions import DemoModule

    settings = build_test_settings(str(tmp_path / "demo.db"))
    app = ApplicationBuilder(settings, plugins=[DemoModule()]).build_app()
    with TestClient(app) as client:
        hello = client.get("/hello")
        assert hello.status_code == 200
        assert "Hello from a plugin!" in hello.text
        jobs = [entry["job"] for entry in client.get("/healthz").json()["scheduler"]]
        assert "demo-heartbeat" in jobs


def test_cli_scaffolds_a_valid_project(tmp_path, capsys) -> None:
    exit_code = cli_main(["new", "mysite", "--dir", str(tmp_path)])
    assert exit_code == 0
    project = tmp_path / "mysite"

    expected = ["mysite/main.py", "mysite/extensions.py", "tests/test_smoke.py",
                ".env", "requirements.txt", "Dockerfile", "run.py", "README.md"]
    for relative in expected:
        assert (project / relative).is_file(), relative

    # Placeholders fully substituted + a real generated secret.
    env = (project / ".env").read_text(encoding="utf-8")
    assert "__SECRET__" not in env and "__PROJECT__" not in env
    assert "SECURITY_SECRET_KEY=" in env and "change-me" not in env
    main_py = (project / "mysite" / "main.py").read_text(encoding="utf-8")
    assert "from mysite.extensions import ProjectModule" in main_py

    # Every generated python file is syntactically valid.
    for py_file in project.rglob("*.py"):
        py_compile.compile(str(py_file), doraise=True)

    # Guard rails: bad names and existing targets are rejected.
    assert cli_main(["new", "Bad-Name", "--dir", str(tmp_path)]) == 1
    assert cli_main(["new", "mysite", "--dir", str(tmp_path)]) == 1


def test_cli_version(capsys) -> None:
    assert cli_main(["version"]) == 0
    assert "pywebfw 0.2" in capsys.readouterr().out
