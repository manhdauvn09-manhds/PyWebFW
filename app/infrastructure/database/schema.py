"""Schema bootstrap + lightweight migrations + seed data.

A migration tool (alembic-style) can replace this later; the framework only
depends on `SchemaInitializer.ensure()` being idempotent. DDL is written for
SQLite and transparently transformed for PostgreSQL via the manager's dialect.
"""
from __future__ import annotations

from app.core.exceptions import ConflictError, DatabaseError
from app.core.logging import BaseLogger
from app.core.security import PasswordHasher
from app.infrastructure.database.manager import BaseDatabaseManager

_TABLES: dict[str, str] = {
    "users": """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            is_active INTEGER NOT NULL DEFAULT 1,
            token_version INTEGER NOT NULL DEFAULT 0,
            must_change_password INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "menus": """
        CREATE TABLE IF NOT EXISTS menus (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            area TEXT NOT NULL DEFAULT 'public',
            parent_id INTEGER NULL REFERENCES menus(id) ON DELETE CASCADE,
            position INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "audit_logs": """
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT 'info',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "contents": """
        CREATE TABLE IF NOT EXISTS contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            seo_title TEXT NOT NULL DEFAULT '',
            seo_description TEXT NOT NULL DEFAULT '',
            is_published INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "system_settings": """
        CREATE TABLE IF NOT EXISTS system_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL UNIQUE,
            value TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "traffic_stats": """
        CREATE TABLE IF NOT EXISTS traffic_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            path TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(day, path)
        )""",
    "traffic_daily": """
        CREATE TABLE IF NOT EXISTS traffic_daily (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL UNIQUE,
            uniques INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
    "db_connections": """
        CREATE TABLE IF NOT EXISTS db_connections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            driver TEXT NOT NULL DEFAULT 'sqlite',
            dsn TEXT NOT NULL,
            pool_size INTEGER NOT NULL DEFAULT 5,
            idle_timeout_seconds INTEGER NOT NULL DEFAULT 300,
            is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
}

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_menus_area_pos ON menus(area, position)",
    "CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_contents_published ON contents(is_published, slug)",
    "CREATE INDEX IF NOT EXISTS idx_traffic_day ON traffic_stats(day)",
]

# Columns added after the initial release: (table, column, DDL fragment).
_MIGRATION_COLUMNS = [
    ("users", "token_version", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "must_change_password", "INTEGER NOT NULL DEFAULT 0"),
]

# SQLite FTS5 full-text index over contents, kept in sync by triggers.
_FTS_OBJECTS = [
    """CREATE VIRTUAL TABLE IF NOT EXISTS contents_fts USING fts5(
        title, summary, body, content='contents', content_rowid='id')""",
    """CREATE TRIGGER IF NOT EXISTS contents_fts_ai AFTER INSERT ON contents BEGIN
        INSERT INTO contents_fts(rowid, title, summary, body)
        VALUES (new.id, new.title, new.summary, new.body);
    END""",
    """CREATE TRIGGER IF NOT EXISTS contents_fts_ad AFTER DELETE ON contents BEGIN
        INSERT INTO contents_fts(contents_fts, rowid, title, summary, body)
        VALUES ('delete', old.id, old.title, old.summary, old.body);
    END""",
    """CREATE TRIGGER IF NOT EXISTS contents_fts_au AFTER UPDATE ON contents BEGIN
        INSERT INTO contents_fts(contents_fts, rowid, title, summary, body)
        VALUES ('delete', old.id, old.title, old.summary, old.body);
        INSERT INTO contents_fts(rowid, title, summary, body)
        VALUES (new.id, new.title, new.summary, new.body);
    END""",
]

_SEED_CONTENTS = [
    ("about", "About Us", "Who we are", "We build reusable software platforms."),
    ("introduction", "Company Introduction", "Our profile", "Founded to deliver maintainable systems."),
    ("privacy-policy", "Privacy Policy", "How we handle data", "We respect and protect user data."),
    ("terms", "Terms & Conditions", "Rules of use", "By using this site you agree to these terms."),
    ("editorial-policy", "Editorial Policy", "Our standards", "Accuracy, transparency and accountability."),
    ("contact", "Contact", "Reach us", "Email: hello@example.com — Phone: +84 000 000 000."),
]

_SEED_PUBLIC_MENU = [
    ("Home", "/"), ("About", "/about"), ("Search", "/search"), ("Contact", "/contact"),
]
_SEED_ADMIN_MENU = [
    ("Dashboard", "/admin"), ("Users", "/admin/users"), ("Menus", "/admin/menus"),
    ("Contents", "/admin/contents"), ("Jobs", "/admin/jobs"),
    ("Settings", "/admin/settings"), ("Logs", "/admin/logs"),
    ("DB Connections", "/admin/db-connections"),
]


class SchemaInitializer:
    def __init__(self, db: BaseDatabaseManager, hasher: PasswordHasher, logger: BaseLogger) -> None:
        self._db = db
        self._hasher = hasher
        self._logger = logger

    def ensure(self) -> None:
        with self._db.transaction():
            for ddl in _TABLES.values():
                self._db.execute(self._adapt(ddl))
            for ddl in _INDEXES:
                self._db.execute(ddl)
        self._migrate()
        self._ensure_fts()
        try:
            with self._db.transaction():
                self._seed()
        except (DatabaseError, ConflictError):
            # Several containers can boot against the same DB at once; the
            # first one wins the seed, the others just continue.
            self._logger.warning("seed skipped — another instance seeded first")
        self._logger.info("database schema ensured")

    def _adapt(self, ddl: str) -> str:
        """Transforms SQLite-flavoured DDL for other engines."""
        if self._db.dialect == "postgres":
            return ddl.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
        return ddl

    def _migrate(self) -> None:
        """Adds columns introduced after the initial release (idempotent)."""
        if self._db.dialect == "sqlite":
            for table, column, ddl in _MIGRATION_COLUMNS:
                existing = {row["name"] for row in
                            self._db.fetch_all(f"PRAGMA table_info({table})")}
                if column not in existing:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    self._logger.info("migrated column", table=table, column=column)
        else:
            for table, column, ddl in _MIGRATION_COLUMNS:
                self._db.execute(
                    f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}")

    def _ensure_fts(self) -> None:
        """Full-text search objects (SQLite FTS5 only — Postgres would use
        tsvector). On failure the search layer falls back to LIKE."""
        if self._db.dialect != "sqlite":
            return
        try:
            with self._db.transaction():
                for ddl in _FTS_OBJECTS:
                    self._db.execute(ddl)
                # Rebuild from the content table: cheap, idempotent, and it
                # covers rows inserted before FTS existed.
                self._db.execute("INSERT INTO contents_fts(contents_fts) VALUES('rebuild')")
        except DatabaseError as exc:
            self._logger.warning("FTS5 unavailable, search falls back to LIKE",
                                 error=exc.message)

    def _seed(self) -> None:
        from app.domain.models import utc_now_iso

        now = utc_now_iso()
        row = self._db.fetch_one("SELECT COUNT(*) AS n FROM users")
        if row and row["n"] == 0:
            # First-boot credentials; must_change_password forces a reset on
            # first login before any other admin action is allowed.
            self._db.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active,"
                " must_change_password, created_at, updated_at)"
                " VALUES (?, ?, ?, 'admin', 1, 1, ?, ?)",
                ("admin", "admin@example.com", self._hasher.hash("ChangeMe!123"), now, now),
            )
        row = self._db.fetch_one("SELECT COUNT(*) AS n FROM menus")
        if row and row["n"] == 0:
            for pos, (title, url) in enumerate(_SEED_PUBLIC_MENU):
                self._db.execute(
                    "INSERT INTO menus (title, url, area, position, created_at, updated_at)"
                    " VALUES (?, ?, 'public', ?, ?, ?)", (title, url, pos, now, now))
            for pos, (title, url) in enumerate(_SEED_ADMIN_MENU):
                self._db.execute(
                    "INSERT INTO menus (title, url, area, position, created_at, updated_at)"
                    " VALUES (?, ?, 'admin', ?, ?, ?)", (title, url, pos, now, now))
        row = self._db.fetch_one("SELECT COUNT(*) AS n FROM contents")
        if row and row["n"] == 0:
            for slug, title, summary, body in _SEED_CONTENTS:
                self._db.execute(
                    "INSERT INTO contents (slug, title, summary, body, seo_title, seo_description,"
                    " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (slug, title, summary, body, title, summary, now, now))
        row = self._db.fetch_one("SELECT COUNT(*) AS n FROM system_settings")
        if row and row["n"] == 0:
            from app.services.site_settings_service import KNOWN_SETTINGS
            for key, default in KNOWN_SETTINGS.items():
                self._db.execute(
                    "INSERT INTO system_settings (key, value, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?)", (key, default, now, now))
