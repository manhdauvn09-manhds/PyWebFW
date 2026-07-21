"""Environment-based, immutable, composed settings objects.

Design: composition — `AppSettings` *has* DatabaseSettings, SecuritySettings, ...
Each group is a frozen dataclass so configuration cannot mutate at runtime.
`EnvironmentReader` encapsulates all raw env/.env access (single responsibility).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# Deployable modules. One image can run any subset (APP_MODULES env), so the
# same codebase deploys as a single server or as separate FE/Admin/Scheduler
# containers depending on the target system.
MODULE_PUBLIC = "public"
MODULE_ADMIN = "admin"
MODULE_SCHEDULER = "scheduler"
KNOWN_MODULES = frozenset({MODULE_PUBLIC, MODULE_ADMIN, MODULE_SCHEDULER})


class EnvironmentReader:
    """Reads typed values from process env, optionally pre-loading a .env file."""

    def __init__(self, dotenv_path: Path | None = None) -> None:
        self._values: dict[str, str] = {}
        if dotenv_path and dotenv_path.exists():
            self._values.update(self._parse_dotenv(dotenv_path))
        self._values.update(os.environ)

    @staticmethod
    def _parse_dotenv(path: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.split("#")[0].strip().strip('"').strip("'")
        return result

    def text(self, key: str, default: str) -> str:
        return self._values.get(key, default)

    def integer(self, key: str, default: int) -> int:
        raw = self._values.get(key)
        return int(raw) if raw and raw.isdigit() else default

    def boolean(self, key: str, default: bool) -> bool:
        raw = self._values.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    path: str
    pool_size: int
    idle_timeout_seconds: int
    driver: str = "sqlite"        # sqlite | postgres
    dsn: str = ""                 # postgres only, e.g. postgresql://user:pw@host/db


@dataclass(frozen=True, slots=True)
class SecuritySettings:
    secret_key: str
    token_ttl_seconds: int
    password_iterations: int


@dataclass(frozen=True, slots=True)
class CacheSettings:
    default_ttl_seconds: int
    backend: str = "memory"       # memory | redis
    redis_url: str = ""


@dataclass(frozen=True, slots=True)
class SchedulerSettings:
    enabled: bool
    tick_seconds: int


@dataclass(frozen=True, slots=True)
class MailSettings:
    host: str = ""                 # empty -> NullMailer (log only)
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True
    from_address: str = "noreply@example.com"
    admin_email: str = ""          # recipient for system notifications


@dataclass(frozen=True, slots=True)
class MediaSettings:
    dir: str = "data/media"
    max_upload_mb: int = 5


@dataclass(frozen=True, slots=True)
class RateLimitSettings:
    max_requests: int
    window_seconds: int
    # Stricter, separate window for the admin login endpoint (anti brute-force).
    login_max_requests: int
    login_window_seconds: int


@dataclass(frozen=True, slots=True)
class AppSettings:
    name: str
    environment: str
    debug: bool
    host: str
    port: int
    database: DatabaseSettings
    security: SecuritySettings
    cache: CacheSettings
    scheduler: SchedulerSettings
    rate_limit: RateLimitSettings
    modules: frozenset[str] = field(default=KNOWN_MODULES)
    mail: MailSettings = field(default_factory=MailSettings)
    media: MediaSettings = field(default_factory=MediaSettings)

    def __post_init__(self) -> None:
        if not self.modules:
            raise RuntimeError("APP_MODULES must enable at least one module")
        unknown = self.modules - KNOWN_MODULES
        if unknown:
            raise RuntimeError(
                f"Unknown APP_MODULES: {', '.join(sorted(unknown))} "
                f"(known: {', '.join(sorted(KNOWN_MODULES))})")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def has_module(self, name: str) -> bool:
        return name in self.modules


class SettingsFactory:
    """Builds the settings tree from the environment (Factory pattern)."""

    def __init__(self, reader: EnvironmentReader) -> None:
        self._env = reader

    def build(self) -> AppSettings:
        env = self._env
        modules_raw = env.text("APP_MODULES", ",".join(sorted(KNOWN_MODULES)))
        modules = frozenset(m.strip().lower() for m in modules_raw.split(",") if m.strip())
        settings = AppSettings(
            modules=modules,
            mail=MailSettings(
                host=env.text("MAIL_HOST", ""),
                port=env.integer("MAIL_PORT", 587),
                username=env.text("MAIL_USERNAME", ""),
                password=env.text("MAIL_PASSWORD", ""),
                use_tls=env.boolean("MAIL_USE_TLS", True),
                from_address=env.text("MAIL_FROM", "noreply@example.com"),
                admin_email=env.text("MAIL_ADMIN_EMAIL", ""),
            ),
            media=MediaSettings(
                dir=env.text("MEDIA_DIR", "data/media"),
                max_upload_mb=env.integer("MEDIA_MAX_UPLOAD_MB", 5),
            ),
            name=env.text("APP_NAME", "PyWebFW"),
            environment=env.text("APP_ENV", "development"),
            debug=env.boolean("APP_DEBUG", False),
            host=env.text("APP_HOST", "127.0.0.1"),
            port=env.integer("APP_PORT", 8000),
            database=DatabaseSettings(
                path=env.text("DB_PATH", "data/app.db"),
                pool_size=env.integer("DB_POOL_SIZE", 20),
                idle_timeout_seconds=env.integer("DB_IDLE_TIMEOUT_SECONDS", 300),
                driver=env.text("DB_DRIVER", "sqlite"),
                dsn=env.text("DB_DSN", ""),
            ),
            security=SecuritySettings(
                secret_key=env.text("SECURITY_SECRET_KEY", ""),
                token_ttl_seconds=env.integer("SECURITY_TOKEN_TTL_SECONDS", 3600),
                password_iterations=env.integer("SECURITY_PASSWORD_ITERATIONS", 310_000),
            ),
            cache=CacheSettings(
                default_ttl_seconds=env.integer("CACHE_DEFAULT_TTL_SECONDS", 120),
                backend=env.text("CACHE_BACKEND", "memory"),
                redis_url=env.text("CACHE_REDIS_URL", "redis://localhost:6379/0"),
            ),
            scheduler=SchedulerSettings(
                enabled=env.boolean("SCHEDULER_ENABLED", True),
                tick_seconds=env.integer("SCHEDULER_TICK_SECONDS", 5),
            ),
            rate_limit=RateLimitSettings(
                max_requests=env.integer("RATELIMIT_MAX_REQUESTS", 120),
                window_seconds=env.integer("RATELIMIT_WINDOW_SECONDS", 60),
                login_max_requests=env.integer("RATELIMIT_LOGIN_MAX_REQUESTS", 3),
                login_window_seconds=env.integer("RATELIMIT_LOGIN_WINDOW_SECONDS", 180),
            ),
        )
        self._validate(settings)
        return settings

    @staticmethod
    def _validate(settings: AppSettings) -> None:
        key = settings.security.secret_key
        if not key or len(key) < 32:
            raise RuntimeError(
                "SECURITY_SECRET_KEY must be set to a random string ≥32 characters")
        if key == "dev-only-insecure-secret":
            raise RuntimeError(
                "SECURITY_SECRET_KEY is still the default insecure value — set a real secret")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return SettingsFactory(EnvironmentReader(Path(".env"))).build()
