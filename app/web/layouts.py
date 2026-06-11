"""Page layouts (Template Method): `render()` fixes the document skeleton,
subclasses customize chrome (header/nav/footer) and styling hooks."""
from __future__ import annotations

from abc import ABC
from typing import Sequence

from app.domain.models import MenuItem
from app.web.components import (
    BreadcrumbsComponent,
    FooterComponent,
    HeaderComponent,
    NavigationComponent,
    SeoMeta,
)

_BASE_CSS = """
body{font-family:system-ui,sans-serif;margin:0;color:#222;line-height:1.5}
main{max-width:960px;margin:0 auto;padding:1rem}
.site-header{padding:1rem;background:#1f2937;color:#fff}
.site-header .brand{color:#fff;font-weight:700;font-size:1.2rem;text-decoration:none}
.site-nav{padding:.5rem 1rem;background:#374151}
.site-nav a{color:#e5e7eb;text-decoration:none;margin-right:.25rem}
.site-nav a.active{font-weight:700;text-decoration:underline}
.breadcrumbs{font-size:.85rem;color:#6b7280;margin:.75rem 0}
.site-footer{margin-top:2rem;padding:1rem;background:#f3f4f6;font-size:.85rem;text-align:center}
.data-table{width:100%;border-collapse:collapse;margin:1rem 0}
.data-table th,.data-table td{border:1px solid #d1d5db;padding:.4rem .6rem;text-align:left}
.stat-card{display:inline-block;border:1px solid #d1d5db;border-radius:8px;
padding:1rem;margin:.5rem;min-width:140px;text-align:center}
.stat-value{font-size:1.6rem;font-weight:700}
.app-form label{display:block;margin:.5rem 0}
.app-form input{display:block;width:280px;padding:.35rem}
.app-form input[type=checkbox]{display:inline-block;width:auto}
.app-form textarea{display:block;width:480px;max-width:100%;height:160px;padding:.35rem}
.search-form input{padding:.4rem;width:260px}
.hp-field{position:absolute;left:-9999px;top:-9999px}
.admin-badge{background:#b91c1c;color:#fff;padding:.1rem .5rem;border-radius:4px;font-size:.75rem}
"""


class BaseLayout(ABC):
    """Owns the HTML document skeleton; subclasses provide the chrome."""

    def __init__(self, site_name: str, menu_items: Sequence[MenuItem]) -> None:
        self._site_name = site_name
        self._menu_items = menu_items

    # --- hooks subclasses may override ------------------------------------
    def _header(self) -> str:
        return HeaderComponent(self._site_name).render()

    def _navigation(self, active_url: str) -> str:
        return NavigationComponent(self._menu_items, active_url).render()

    def _footer(self) -> str:
        return FooterComponent(self._site_name).render()

    # --- fixed skeleton ----------------------------------------------------
    def render(self, *, seo: SeoMeta, content: str, active_url: str = "",
               breadcrumbs: Sequence[tuple[str, str]] = (), nonce: str = "") -> str:
        # The CSP only allows inline style/script blocks carrying this nonce.
        nonce_attr = f' nonce="{nonce}"' if nonce else ""
        return (
            "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"{seo.render()}\n<style{nonce_attr}>{_BASE_CSS}</style>\n</head>\n<body>\n"
            f"{self._header()}\n{self._navigation(active_url)}\n"
            f"<main>\n{BreadcrumbsComponent(breadcrumbs).render()}\n{content}\n</main>\n"
            f"{self._footer()}\n</body>\n</html>"
        )


class PublicLayout(BaseLayout):
    def _footer(self) -> str:
        links = [
            ("Privacy Policy", "/privacy-policy"),
            ("Terms", "/terms"),
            ("Editorial Policy", "/editorial-policy"),
            ("Sitemap", "/sitemap"),
            ("RSS", "/rss"),
        ]
        return FooterComponent(self._site_name, links).render()


class AdminLayout(BaseLayout):
    def _header(self) -> str:
        return (f'<header class="site-header"><a class="brand" href="/admin">'
                f'{self._site_name} <span class="admin-badge">ADMIN</span></a></header>')

    def _footer(self) -> str:
        return FooterComponent(f"{self._site_name} Administration").render()
