"""Standardized API response envelope.

Every JSON endpoint returns the same shape:
    {"success": bool, "data": ..., "error": {...}|null, "meta": {...}|null}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pywebfw.core.pagination import PageResult

T = TypeVar("T")


class BaseResponse(ABC):
    """Contract: anything the API layer returns can serialize itself."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ApiResponse(BaseResponse, Generic[T]):
    success: bool
    data: T | None = None
    error: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    @classmethod
    def ok(cls, data: T | None = None, meta: dict[str, Any] | None = None) -> "ApiResponse[T]":
        return cls(success=True, data=data, meta=meta)

    @classmethod
    def fail(cls, code: str, message: str, details: Any = None) -> "ApiResponse[None]":
        error: dict[str, Any] = {"code": code, "message": message}
        if details is not None:
            error["details"] = details
        return cls(success=False, error=error)

    @classmethod
    def paginated(cls, result: PageResult, serializer=lambda x: x) -> "ApiResponse[list]":
        return cls(
            success=True,
            data=[serializer(item) for item in result.items],
            meta={"page": result.page, "size": result.size, "total": result.total, "pages": result.pages},
        )

    def to_dict(self) -> dict[str, Any]:
        return {"success": self.success, "data": self.data, "error": self.error, "meta": self.meta}
