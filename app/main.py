"""ASGI entry point: `uvicorn app.main:app`."""
from __future__ import annotations

from app.bootstrap import ApplicationBuilder

app = ApplicationBuilder().build_app()
