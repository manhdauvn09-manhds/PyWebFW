"""ASGI entry point: `uvicorn app.main:app`."""
from __future__ import annotations

from pywebfw.bootstrap import ApplicationBuilder

from app.extensions import DemoModule

app = ApplicationBuilder(plugins=[DemoModule()]).build_app()
