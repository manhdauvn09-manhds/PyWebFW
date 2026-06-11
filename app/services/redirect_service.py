"""URL redirects (301/302) — SEO-safe slug changes and moved pages.

The active rule set is cached (60s TTL, invalidated on every change); lookups
happen only on the 404 path so normal requests pay nothing.
"""
from __future__ import annotations

from app.core.exceptions import ConflictError, ValidationFailedError
from app.core.pagination import PageRequest, PageResult
from app.domain.models import Redirect
from app.infrastructure.cache.manager import BaseCacheManager
from app.repositories.log_repository import LogRepository
from app.repositories.redirect_repository import RedirectRepository
from app.services.base import AuditMixin, BaseService

_CACHE_KEY = "redirects:active"
_ALLOWED_STATUS = (301, 302)


class RedirectService(BaseService, AuditMixin):
    def __init__(self, redirects: RedirectRepository, cache: BaseCacheManager,
                 logs: LogRepository) -> None:
        super().__init__()
        self._redirects = redirects
        self._cache = cache
        self._audit_repo = logs

    # --- resolution (called from the 404 path) ---------------------------------
    def resolve(self, path: str) -> tuple[str, int] | None:
        """Returns (target, status_code) when an active rule matches."""
        mapping: dict[str, tuple[str, int, int]] = self._cache.get_or_set(
            _CACHE_KEY,
            lambda: {r.from_path: (r.to_path, r.status_code, r.id)
                     for r in self._redirects.list_active()},
            ttl_seconds=60,
        )
        match = mapping.get(path)
        if match is None:
            return None
        to_path, status_code, redirect_id = match
        self._redirects.increment_hits(redirect_id)
        return to_path, status_code

    # --- management ---------------------------------------------------------------
    def list_redirects(self, page: PageRequest) -> PageResult[Redirect]:
        return self._redirects.list_page(page)

    def create(self, redirect: Redirect, actor: str) -> Redirect:
        self._validate(redirect)
        if self._redirects.find_by_from_path(redirect.from_path):
            raise ConflictError(f"A redirect from '{redirect.from_path}' already exists")
        self._redirects.add(redirect)
        self._invalidate(actor, "redirect.created", redirect)
        return redirect

    def delete(self, redirect_id: int, actor: str) -> None:
        redirect = self._redirects.get_by_id(redirect_id)
        self._redirects.delete(redirect_id)
        self._invalidate(actor, "redirect.deleted", redirect)

    def auto_create(self, from_path: str, to_path: str, actor: str = "system") -> None:
        """Idempotent upsert used by the content.slug_changed event handler."""
        if from_path == to_path:
            return
        existing = self._redirects.find_by_from_path(from_path)
        if existing:
            existing.to_path = to_path
            self._redirects.update(existing)
            self._invalidate(actor, "redirect.updated", existing)
            return
        self.create(Redirect(from_path=from_path, to_path=to_path), actor)

    # --- helpers ---------------------------------------------------------------------
    @staticmethod
    def _validate(redirect: Redirect) -> None:
        if not redirect.from_path.startswith("/") or not redirect.to_path.startswith("/"):
            raise ValidationFailedError("Paths must start with '/'")
        if redirect.from_path == redirect.to_path:
            raise ValidationFailedError("Source and target must differ")
        if redirect.from_path == "/":
            raise ValidationFailedError("Cannot redirect the home page")
        if redirect.status_code not in _ALLOWED_STATUS:
            raise ValidationFailedError("Status code must be 301 or 302")

    def _invalidate(self, actor: str, action: str, redirect: Redirect) -> None:
        self._cache.delete(_CACHE_KEY)
        self._audit(actor, action, target=redirect.from_path,
                    detail=f"-> {redirect.to_path}")
