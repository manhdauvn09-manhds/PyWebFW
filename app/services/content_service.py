"""Content: public read path (CMS pages, sitemap, RSS) + admin management
(CRUD with audit trail and cache invalidation)."""
from __future__ import annotations

from app.core.exceptions import ConflictError, NotFoundError
from app.core.pagination import PageRequest, PageResult
from app.domain.models import ContentItem
from app.infrastructure.cache.manager import BaseCacheManager
from app.repositories.content_repository import ContentRepository
from app.repositories.log_repository import LogRepository
from app.services.base import AuditMixin, BaseService

_CACHE_PREFIX = "content:"


class ContentService(BaseService, AuditMixin):
    def __init__(self, contents: ContentRepository, cache: BaseCacheManager,
                 logs: LogRepository) -> None:
        super().__init__()
        self._contents = contents
        self._cache = cache
        self._audit_repo = logs

    # --- public read path -----------------------------------------------------
    def get_page(self, slug: str) -> ContentItem:
        item = self._cache.get_or_set(
            f"{_CACHE_PREFIX}{slug}",
            lambda: self._contents.find_published_by_slug(slug),
            ttl_seconds=300,
        )
        if item is None:
            raise NotFoundError(f"Content '{slug}' not found")
        return item

    def sitemap_entries(self) -> list[dict[str, str]]:
        static_urls = ["/", "/search", "/rss"]
        entries = [{"loc": url, "changefreq": "weekly"} for url in static_urls]
        for item in self._contents.list_published():
            entries.append({"loc": f"/{item.slug}", "changefreq": "monthly",
                            "lastmod": item.updated_at})
        return entries

    def rss_items(self, limit: int = 20) -> list[ContentItem]:
        return self._contents.list_published()[:limit]

    # --- admin management -------------------------------------------------------
    def list_contents(self, page: PageRequest) -> PageResult[ContentItem]:
        return self._contents.list_page(page)

    def get(self, content_id: int) -> ContentItem:
        return self._contents.get_by_id(content_id)

    def create(self, item: ContentItem, actor: str) -> ContentItem:
        if self._contents.slug_exists(item.slug):
            raise ConflictError(f"Slug '{item.slug}' already exists")
        self._contents.add(item)
        self._invalidate(item.slug)
        self._audit(actor, "content.created", target=item.slug)
        return item

    def update(self, item: ContentItem, actor: str) -> ContentItem:
        assert item.id is not None
        existing = self._contents.get_by_id(item.id)
        if self._contents.slug_exists(item.slug, exclude_id=item.id):
            raise ConflictError(f"Slug '{item.slug}' already exists")
        item.created_at = existing.created_at
        self._contents.update(item)
        self._invalidate(existing.slug)   # old slug may have changed
        self._invalidate(item.slug)
        self._audit(actor, "content.updated", target=item.slug)
        return item

    def delete(self, content_id: int, actor: str) -> None:
        existing = self._contents.get_by_id(content_id)
        self._contents.delete(content_id)
        self._invalidate(existing.slug)
        self._audit(actor, "content.deleted", target=existing.slug, level="warning")

    def _invalidate(self, slug: str) -> None:
        self._cache.delete(f"{_CACHE_PREFIX}{slug}")
