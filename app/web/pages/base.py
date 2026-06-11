"""Page object model.

`BasePage` (Template Method): `render()` is fixed — it asks subclasses for
title/SEO/breadcrumbs/content and hands them to the layout. `PublicPage` and
`AdminPage` bind the right layout + breadcrumb root; concrete pages only
implement `build_content()` (and override `seo()` when they need richer meta).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

from app.domain.models import MenuItem
from app.infrastructure.auth.manager import CurrentUser
from app.web.components import SeoMeta
from app.web.layouts import AdminLayout, BaseLayout, PublicLayout


@dataclass(frozen=True, slots=True)
class PageContext:
    """Everything a page needs from the current request."""

    site_name: str
    path: str
    menu_items: Sequence[MenuItem] = ()
    query: dict[str, str] = field(default_factory=dict)
    user: CurrentUser | None = None
    csp_nonce: str = ""   # per-request nonce for inline <style>/<script>


class BasePage(ABC):
    def __init__(self, ctx: PageContext) -> None:
        self._ctx = ctx

    # --- contract ----------------------------------------------------------
    @property
    @abstractmethod
    def title(self) -> str: ...

    @abstractmethod
    def build_content(self) -> str: ...

    # --- overridable defaults ----------------------------------------------
    def seo(self) -> SeoMeta:
        return SeoMeta(title=f"{self.title} — {self._ctx.site_name}")

    def breadcrumbs(self) -> list[tuple[str, str]]:
        return [("Home", "/"), (self.title, "")]

    @abstractmethod
    def _layout(self) -> BaseLayout: ...

    # --- fixed rendering pipeline (Template Method) --------------------------
    def render(self) -> str:
        return self._layout().render(
            seo=self.seo(),
            content=self.build_content(),
            active_url=self._ctx.path,
            breadcrumbs=self.breadcrumbs(),
            nonce=self._ctx.csp_nonce,
        )


class PublicPage(BasePage):
    def _layout(self) -> BaseLayout:
        return PublicLayout(self._ctx.site_name, self._ctx.menu_items)


class AdminPage(BasePage):
    """Base for all admin screens; bootstrap guarantees an authenticated
    admin user before any AdminPage is instantiated."""

    def _layout(self) -> BaseLayout:
        return AdminLayout(self._ctx.site_name, self._ctx.menu_items)

    def seo(self) -> SeoMeta:
        # Admin screens must never be indexed.
        return SeoMeta(title=f"{self.title} — Admin", robots="noindex, nofollow")

    def breadcrumbs(self) -> list[tuple[str, str]]:
        return [("Admin", "/admin"), (self.title, "")]
