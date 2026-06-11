"""System settings: key-value configuration editable at runtime from the
admin area (no redeploy). Read-through cached; writes are whitelisted to the
known-key registry, audited, and invalidate the cache immediately."""
from __future__ import annotations

from pywebfw.core.exceptions import ValidationFailedError
from pywebfw.infrastructure.cache.manager import BaseCacheManager
from pywebfw.repositories.log_repository import LogRepository
from pywebfw.repositories.setting_repository import SettingRepository
from pywebfw.services.base import AuditMixin, BaseService

# key -> default value. Extending the framework = adding one entry here.
KNOWN_SETTINGS: dict[str, str] = {
    "site_tagline": "",
    "footer_text": "",
    "seo_default_description": "",
    "maintenance_mode": "0",       # "1" = public site returns 503
}

_CACHE_KEY = "settings:all"
_MAX_VALUE_LENGTH = 1000


class SiteSettingsService(BaseService, AuditMixin):
    def __init__(self, settings_repo: SettingRepository, cache: BaseCacheManager,
                 logs: LogRepository) -> None:
        super().__init__()
        self._repo = settings_repo
        self._cache = cache
        self._audit_repo = logs

    def all(self) -> dict[str, str]:
        """Known settings with stored values overriding defaults."""
        stored = self._cache.get_or_set(_CACHE_KEY, self._repo.all_as_dict,
                                        ttl_seconds=30)
        return {key: stored.get(key, default) for key, default in KNOWN_SETTINGS.items()}

    def get(self, key: str, default: str = "") -> str:
        return self.all().get(key, default)

    def is_maintenance(self) -> bool:
        return self.get("maintenance_mode", "0") == "1"

    def update(self, values: dict[str, str], actor: str) -> dict[str, str]:
        unknown = set(values) - set(KNOWN_SETTINGS)
        if unknown:
            raise ValidationFailedError(
                f"Unknown setting(s): {', '.join(sorted(unknown))}")
        for key, value in values.items():
            if len(value) > _MAX_VALUE_LENGTH:
                raise ValidationFailedError(f"Setting '{key}' is too long")
            self._repo.upsert(key, value)
        self._cache.delete(_CACHE_KEY)
        self._audit(actor, "settings.updated", target=", ".join(sorted(values)))
        return self.all()
