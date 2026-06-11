"""Public-facing pages.

Inheritance in action:
    PublicPage -> HomePage / SearchPage / SitemapPage
    PublicPage -> ContentPage -> AboutPage, ContactPage, PrivacyPolicyPage, ...
`ContentPage` handles every CMS-backed screen once (DRY); subclasses just bind
their slug.
"""
from __future__ import annotations

from app.core.pagination import PageRequest
from app.services.content_service import ContentService
from app.services.search_service import SearchService
from app.web.components import SearchFormWidget, SeoMeta, esc
from app.web.pages.base import PageContext, PublicPage


class HomePage(PublicPage):
    def __init__(self, ctx: PageContext, contents: ContentService) -> None:
        super().__init__(ctx)
        self._contents = contents

    @property
    def title(self) -> str:
        return "Home"

    def breadcrumbs(self) -> list[tuple[str, str]]:
        return []

    def seo(self) -> SeoMeta:
        return SeoMeta(
            title=self._ctx.site_name,
            description="Welcome to our platform — news, insights and more.",
            canonical="/",
        )

    def build_content(self) -> str:
        cards = "".join(
            f'<li><a href="/{esc(item.slug)}">{esc(item.title)}</a> — {esc(item.summary)}</li>'
            for item in self._contents.rss_items(limit=6)
        )
        return (f"<h1>Welcome to {esc(self._ctx.site_name)}</h1>"
                f"{SearchFormWidget().render()}"
                f"<h2>Latest content</h2><ul>{cards}</ul>")


class ContentPage(PublicPage):
    """Generic CMS-backed page; subclasses pin the slug."""

    slug: str = ""

    def __init__(self, ctx: PageContext, contents: ContentService) -> None:
        super().__init__(ctx)
        self._item = contents.get_page(self.slug)

    @property
    def title(self) -> str:
        return self._item.title

    def seo(self) -> SeoMeta:
        return SeoMeta(
            title=self._item.seo_title or self._item.title,
            description=self._item.seo_description,
            canonical=f"/{self.slug}",
        )

    def build_content(self) -> str:
        return (f"<article><h1>{esc(self._item.title)}</h1>"
                f"<p><em>{esc(self._item.summary)}</em></p>"
                f"<div>{esc(self._item.body)}</div></article>")


class AboutPage(ContentPage):
    slug = "about"


class IntroductionPage(ContentPage):
    slug = "introduction"


class ContactPage(ContentPage):
    slug = "contact"


class PrivacyPolicyPage(ContentPage):
    slug = "privacy-policy"


class TermsPage(ContentPage):
    slug = "terms"


class EditorialPolicyPage(ContentPage):
    slug = "editorial-policy"


class SitemapPage(PublicPage):
    """Human-readable sitemap (the XML variant lives in the public API)."""

    def __init__(self, ctx: PageContext, contents: ContentService) -> None:
        super().__init__(ctx)
        self._contents = contents

    @property
    def title(self) -> str:
        return "Sitemap"

    def build_content(self) -> str:
        links = "".join(
            f'<li><a href="{esc(e["loc"])}">{esc(e["loc"])}</a></li>'
            for e in self._contents.sitemap_entries()
        )
        return f"<h1>Sitemap</h1><ul>{links}</ul>"


class SearchPage(PublicPage):
    def __init__(self, ctx: PageContext, search: SearchService) -> None:
        super().__init__(ctx)
        self._search = search

    @property
    def title(self) -> str:
        return "Search"

    def seo(self) -> SeoMeta:
        # Search result pages should not be indexed (duplicate-content risk).
        return SeoMeta(title=f"Search — {self._ctx.site_name}", robots="noindex, follow")

    def build_content(self) -> str:
        query = self._ctx.query.get("q", "")
        page = int(self._ctx.query.get("page", "1") or 1)
        parts = [f"<h1>Search</h1>{SearchFormWidget(query).render()}"]
        if query:
            result = self._search.search(query, PageRequest.create(page=page))
            parts.append(f"<p>{result.total} result(s) for <strong>{esc(query)}</strong></p>")
            parts.append("<ul>" + "".join(
                f'<li><a href="/{esc(i.slug)}">{esc(i.title)}</a> — {esc(i.summary)}</li>'
                for i in result.items) + "</ul>")
        return "".join(parts)
