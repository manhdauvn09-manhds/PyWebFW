"""Pagination / sorting value objects shared by repositories and API layer."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")

MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class PageRequest:
    page: int = 1
    size: int = 20
    sort_by: str | None = None
    sort_desc: bool = False

    @classmethod
    def create(
        cls,
        page: int = 1,
        size: int = 20,
        sort_by: str | None = None,
        sort_desc: bool = False,
    ) -> "PageRequest":
        """Clamps untrusted query input into safe bounds."""
        return cls(
            page=max(1, page),
            size=min(max(1, size), MAX_PAGE_SIZE),
            sort_by=sort_by,
            sort_desc=sort_desc,
        )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


@dataclass(frozen=True)
class PageResult(Generic[T]):
    items: list[T] = field(default_factory=list)
    total: int = 0
    page: int = 1
    size: int = 20

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.size)) if self.size else 1

    def map(self, fn) -> "PageResult":
        return PageResult(items=[fn(i) for i in self.items], total=self.total, page=self.page, size=self.size)
