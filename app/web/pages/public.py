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


class DynamicContentPage(ContentPage):
    """Serves any published content by slug (the catch-all route) — content
    created in the admin CMS is reachable without code changes."""

    def __init__(self, ctx: PageContext, contents: ContentService, slug: str) -> None:
        self.slug = slug          # instance attr set before ContentPage loads it
        super().__init__(ctx, contents)


class AboutPage(ContentPage):
    slug = "about"


class IntroductionPage(ContentPage):
    slug = "introduction"


_CONTACT_FORM_SCRIPT = """
<script nonce="__NONCE__">
const form = document.getElementById('contact-form');
form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = form.querySelector('.form-message');
  const el = (name) => form.elements[name];
  const res = await fetch('/api/public/contact', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      name: el('name').value, email: el('email').value,
      subject: el('subject').value, message: el('message').value,
      website: el('website').value,
    }),
  });
  const result = await res.json();
  if (result.success) { form.reset(); message.textContent = 'Thank you! Your message has been sent.'; }
  else { message.textContent = result.error?.message || 'Sending failed, please try again.'; }
});
</script>
"""


class ContactPage(ContentPage):
    """CMS intro text + a working contact form (honeypot + rate-limited API)."""

    slug = "contact"

    def build_content(self) -> str:
        script = _CONTACT_FORM_SCRIPT.replace("__NONCE__", esc(self._ctx.csp_nonce))
        form = (
            "<h2>Send us a message</h2>"
            '<form id="contact-form" class="app-form">'
            '<label>Your name<input name="name" required maxlength="100"></label>'
            '<label>Email<input name="email" type="email" required></label>'
            '<label>Subject<input name="subject" maxlength="150"></label>'
            '<label>Message<textarea name="message" required minlength="10"'
            ' maxlength="5000"></textarea></label>'
            # Honeypot: visually hidden; bots fill it, humans never see it.
            '<div class="hp-field" aria-hidden="true">'
            '<label>Website<input name="website" tabindex="-1" autocomplete="off"></label></div>'
            '<button type="submit">Send message</button>'
            '<div class="form-message" role="status"></div></form>'
        )
        return f"{super().build_content()}{form}{script}"


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
