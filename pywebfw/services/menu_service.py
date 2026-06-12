"""Menu management with read-through cache; mutations invalidate the cache."""
from __future__ import annotations

from pywebfw.core.pagination import PageRequest, PageResult
from pywebfw.domain.models import MenuArea, MenuItem
from pywebfw.infrastructure.cache.manager import BaseCacheManager
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.repositories.menu_repository import MenuRepository
from pywebfw.services.base import AuditMixin, BaseService

_CACHE_PREFIX = "menu:"


class MenuService(BaseService, AuditMixin):
    def __init__(self, menus: MenuRepository, logs: LogRepository, cache: BaseCacheManager) -> None:
        super().__init__()
        self._menus = menus
        self._audit_repo = logs
        self._cache = cache

    def get_menu(self, area: MenuArea) -> list[MenuItem]:
        return self._cache.get_or_set(
            f"{_CACHE_PREFIX}{area.value}",
            lambda: self._menus.list_active_by_area(area),
            ttl_seconds=300,
        )

    def warm_cache(self) -> int:
        """Called by CacheWarmupJob — preloads every menu area."""
        count = 0
        for area in MenuArea:
            items = self._menus.list_active_by_area(area)
            self._cache.set(f"{_CACHE_PREFIX}{area.value}", items, ttl_seconds=300)
            count += len(items)
        return count

    def list_menus(self, page: PageRequest) -> PageResult[MenuItem]:
        return self._menus.list_page(page)

    def get(self, menu_id: int) -> MenuItem:
        return self._menus.get_by_id(menu_id)

    def create(self, item: MenuItem, actor: str) -> MenuItem:
        self._menus.add(item)
        self._invalidate(actor, "menu.created", item)
        return item

    def update(self, item: MenuItem, actor: str) -> MenuItem:
        self._menus.update(item)
        self._invalidate(actor, "menu.updated", item)
        return item

    def delete(self, menu_id: int, actor: str) -> None:
        item = self._menus.get_by_id(menu_id)
        self._menus.delete(menu_id)
        self._invalidate(actor, "menu.deleted", item)

    def _invalidate(self, actor: str, action: str, item: MenuItem) -> None:
        self._cache.delete_prefix(_CACHE_PREFIX)
        self._audit(actor, action, target=item.title)
