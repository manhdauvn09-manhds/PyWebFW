"""Class-based controllers on top of FastAPI's APIRouter.

`BaseController` (Template Method): `build_router()` is fixed; each controller
implements `_register()` to declare its endpoints. Controllers receive their
dependencies (services) by constructor — the DI container wires them at boot.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from fastapi import APIRouter

from pywebfw.core.pagination import PageResult
from pywebfw.core.responses import ApiResponse


class BaseController(ABC):
    prefix: str = ""
    tags: list[str] = []

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix=self.prefix, tags=self.tags or None)
        self._register(router)
        return router

    @abstractmethod
    def _register(self, router: APIRouter) -> None: ...


class BaseApiController(BaseController):
    """Adds the standardized response envelope helpers for JSON endpoints."""

    @staticmethod
    def ok(data=None, meta: dict | None = None) -> dict:
        return ApiResponse.ok(data, meta).to_dict()

    @staticmethod
    def paginated(result: PageResult, serializer=lambda x: x) -> dict:
        return ApiResponse.paginated(result, serializer).to_dict()
