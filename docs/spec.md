# Architecture & Design Specification — PyWebFW v0.2.0

## 1. Layered Clean Architecture

All HTTP traffic passes through a fixed sequence of layers. Each layer has a single responsibility and depends only on the layer below it.

```
┌─────────────────────────────────────────────────────┐
│                  HTTP (FastAPI)                      │
│   Route handlers — parse request, call controller   │
├─────────────────────────────────────────────────────┤
│                  Middleware Stack                    │
│   Auth token → RBAC → Rate-limit → CSP nonce →      │
│   Gzip → Structured access log                      │
├─────────────────────────────────────────────────────┤
│                  Controllers                         │
│   Thin orchestrators — validate input, delegate to  │
│   one or more services, format response             │
├─────────────────────────────────────────────────────┤
│                  Services                            │
│   Business logic — enforce invariants, fire events, │
│   coordinate multiple repositories                  │
├─────────────────────────────────────────────────────┤
│                  Repositories                        │
│   Data access — parameterised SQL only, return      │
│   domain models, map constraint errors to 409       │
├─────────────────────────────────────────────────────┤
│                  Database                            │
│   SQLite (FTS5) via BaseDatabaseManager pool        │
└─────────────────────────────────────────────────────┘
```

Side-effects (email, storage, cache, events) are injected as abstract interfaces at the service layer. No layer below services knows about HTTP.

---

## 2. Key Design Patterns

### 2.1 Dependency Injection Container

`ServiceContainer` (in `core/container.py`) is a type-keyed registry with three registration scopes:

| Scope | Lifetime | Typical Use |
|-------|----------|-------------|
| `singleton` | Process lifetime | DB pool, cache manager, event bus |
| `scoped` | HTTP request | Auth context, per-request audit accumulator |
| `transient` | Each call to `resolve()` | Stateless utilities, throwaway clients |

Registration is exclusively done in `bootstrap.py`. Route handlers receive dependencies via FastAPI `Depends()` wrappers that delegate to `container.resolve(T)`.

No service resolves its own dependencies — all dependencies are declared in `__init__` signatures and satisfied by the container at startup.

### 2.2 Abstract Base Classes (Swap Points)

Every infrastructure concern is fronted by an ABC in `core/interfaces/`. Community and Pro editions swap implementations without touching calling code.

See the full swap table in section 3.

### 2.3 EventBus (Pub/Sub Domain Facts)

`EventBus` (singleton, in `core/events.py`) routes typed domain events to zero or more async subscribers:

- Publishers call `await bus.publish(SomeEvent(...))`.
- Subscribers register with `@bus.subscribe(SomeEvent)` decorator.
- Events are plain dataclasses carrying facts (what happened, correlation ID, timestamp).
- Subscribers are fire-and-forget; publisher is not aware of downstream effects.
- Failure in a subscriber is logged and does not propagate to the publisher.

Typical uses: audit log writes, cache invalidation, email notifications, scheduler-failure alerts.

### 2.4 Plugin System (AppModule ABC)

```python
class AppModule(ABC):
    @abstractmethod
    def mount(self, app: FastAPI, container: ServiceContainer) -> None:
        """Register routes, middleware, services, and subscribers."""
```

Plugins declare themselves in `PLUGIN_MODULES` env var (dotted module path). `bootstrap.py` imports and calls `mount()` for each plugin after the core container is built. Plugins may:

- Add FastAPI routers under any prefix.
- Override container bindings (must re-register before any request resolves them).
- Register additional `BaseSchedulerJob` implementations.
- Subscribe to EventBus events.
- Inject admin navigation items via a `NavRegistry` singleton.

---

## 3. ABC Swap Table

| Interface | Community Impl | Pro Impl (placeholder) |
|-----------|---------------|------------------------|
| `BaseDatabaseManager` | `SQLiteDatabaseManager` (pool, WAL mode) | `PostgresDatabaseManager` |
| `BaseCacheManager` | `InMemoryLRUCache` (TTL, max-entries) | `RedisCacheManager` |
| `BaseAuthHandler` | `HMACSessionAuthHandler` (PBKDF2 + token_version) | `OAuthAuthHandler` (SSO) |
| `BaseMailer` | `SMTPMailer` (stdlib `smtplib`) | `SendGridMailer` |
| `BaseMediaStorage` | `LocalDiskStorage` (filesystem) | `S3CompatibleStorage` (R2/S3) |
| `BaseRepository[T]` | `SQLiteRepository[T]` (generic CRUD + query builder) | inherits SQLite; overrides for Postgres |
| `BaseSchedulerJob` | (each job is a concrete subclass) | additional jobs in Pro plugins |

All interfaces live under `core/interfaces/`. Implementations live under `infrastructure/`. No file outside `infrastructure/` imports a concrete class directly.

---

## 4. Directory Structure

```
project_root/
├── bootstrap.py              # Single composition root (see §5)
├── main.py                   # ASGI entrypoint; imports bootstrap
├── core/
│   ├── container.py          # ServiceContainer
│   ├── events.py             # EventBus, base Event dataclass
│   ├── interfaces/           # All ABCs (one file per interface)
│   ├── models/               # Domain model dataclasses (no DB logic)
│   └── exceptions.py         # Domain exception hierarchy
├── infrastructure/
│   ├── database/             # SQLiteDatabaseManager, migrations
│   ├── cache/                # InMemoryLRUCache
│   ├── auth/                 # HMACSessionAuthHandler
│   ├── mail/                 # SMTPMailer
│   ├── storage/              # LocalDiskStorage
│   └── scheduler/            # asyncio scheduler loop + built-in jobs
├── modules/
│   ├── public/
│   │   ├── controllers/      # FastAPI route handlers
│   │   ├── services/         # PublicContentService, SearchService, etc.
│   │   └── repositories/     # ContentRepository, AnalyticsRepository
│   ├── admin/
│   │   ├── controllers/
│   │   ├── services/
│   │   └── repositories/
│   └── scheduler/
│       ├── runner.py         # SchedulerRunner (asyncio loop)
│       └── jobs/             # One file per built-in job
├── templates/
│   ├── public/               # Jinja2 templates for public site
│   └── admin/                # Jinja2 templates for admin UI
├── static/                   # CSS, JS, images (content-hash filenames)
├── plugins/                  # Optional; Pro or third-party AppModules
└── tests/
    ├── conftest.py           # TestClient fixture, test DB setup
    └── test_*.py             # 15 test files, ~69 end-to-end tests
```

---

## 5. bootstrap.py — Single Composition Root

`bootstrap.py` is the only file permitted to instantiate concrete infrastructure classes. All other code receives interfaces via the container.

Startup sequence:

1. Load and validate environment variables; abort if required secrets are missing.
2. Instantiate `SQLiteDatabaseManager`; run pending migrations.
3. Instantiate `InMemoryLRUCache`, `HMACSessionAuthHandler`, `SMTPMailer`, `LocalDiskStorage`.
4. Build `ServiceContainer`; register all singletons.
5. Instantiate `EventBus`; register core subscribers (audit writer, cache invalidator).
6. For each module in `APP_MODULES`: instantiate its `AppModule` subclass and call `mount(app, container)`.
7. Load plugins from `PLUGIN_MODULES`; call `mount()` for each.
8. If `scheduler` in `APP_MODULES`: instantiate `SchedulerRunner`; register all jobs; start asyncio task.
9. Return the fully configured `FastAPI` application to `main.py`.

Nothing in step 6–9 may fail silently; exceptions propagate and abort startup.

---

## 6. Scheduler Architecture

The scheduler runs as a long-lived asyncio `Task` alongside the FastAPI event loop. It does not use threads.

```
SchedulerRunner (asyncio Task)
│
├── JobRegistry: List[BaseSchedulerJob]
│
└── Main loop (tick every 60 s):
    For each job:
      if job.is_due(now):
        spawn asyncio.create_task(run_with_audit(job))

run_with_audit(job):
  1. Insert scheduler_runs row (status=RUNNING, trigger, started_at)
  2. await job.run()
  3. Update row (status=OK, finished_at, duration_ms)
  4. On exception: update row (status=FAIL, error_msg)
               retry up to max_retries with exponential backoff
               after exhausting retries: bus.publish(JobFailed(job_id, error))
```

Each job runs in its own `Task`; a slow or crashing job does not block other jobs or HTTP request handling.

Jobs declare schedule via a cron expression string (`"0 2 * * *"`) or a `timedelta` interval. The runner evaluates cron expressions using a minimal built-in parser (no external cron library dependency).

---

## 7. Security Architecture

### 7.1 Password Storage
- Algorithm: PBKDF2-HMAC-SHA256
- Iterations: 310 000 (OWASP 2024 minimum)
- Salt: 32 random bytes, per-user, stored alongside hash
- No framework-level password hashing — implemented explicitly in `HMACSessionAuthHandler`

### 7.2 Session Tokens
- Format: `base64(user_id + ":" + token_version + ":" + timestamp) + "." + HMAC-SHA256(payload, SECRET_KEY)`
- `token_version` is an integer column on the `users` table. Incrementing it (on password change, TOTP reset, or explicit logout-all) invalidates every existing token for that user without a token store.
- Tokens transmitted only via `HttpOnly; Secure; SameSite=Strict` cookie.
- Token verified on every request in `AuthMiddleware` before the route handler runs.

### 7.3 Forced Password Change
- `users.must_change_password` boolean flag.
- `AuthMiddleware` detects flag after successful token verification.
- All admin routes except `/admin/change-password` return `403 Forbidden` until flag cleared.
- Flag is cleared atomically with the password update in a single transaction.

### 7.4 TOTP Two-Factor Authentication
- Standard: RFC 6238 (TOTP), HMAC-SHA1, 30-second step, 6-digit code
- Enrollment: server generates a 160-bit random secret; returns QR code URI
- Verification: ±1 step window (tolerates 30 s clock skew)
- Backup codes: 8 codes, each 10 random alphanumeric characters, hashed with PBKDF2 individually
- After successful TOTP verification, a short-lived `totp_verified` flag is set in the session

### 7.5 RBAC
- `roles` and `permissions` tables; `user_roles` and `role_permissions` junction tables
- On each request, after token verification, user's effective permissions loaded from DB (cached in scoped container for request duration)
- Permission check in service layer: `require_permission(ctx, "content:publish")` raises `ForbiddenError` (→ 403) if not satisfied
- No permission checks in route handlers or templates — only in services

### 7.6 Login Throttle
- Failed attempts tracked in `login_attempts` table keyed by IP address and username
- Lockout after 5 failures in 15-minute rolling window (both thresholds configurable in site settings)
- Lockout response is identical to wrong-password response (no enumeration)
- Successful login resets attempt counter for that IP+username pair

### 7.7 SQL Injection Prevention
- All queries use DB-API 2.0 parameterised placeholders (`?`) — no f-strings or `.format()` in SQL
- `SQLiteRepository` base class enforces this via typed `execute(sql, params: tuple)` method; raw SQL string execution is private
- FTS5 queries: each whitespace-delimited token is individually double-quoted (`"token"`) before being joined with `AND` — prevents FTS5 operator injection

### 7.8 XSS Prevention
- Jinja2 `autoescape=True` on all templates globally
- Rich-text body fields: stored as HTML; rendered with `{{ body | safe }}` only after passing through a whitelist-based HTML sanitiser (strips `<script>`, `onerror`, `javascript:` etc.) at write time
- CSP nonce: `AuthMiddleware` generates a random 16-byte nonce per request; injects it into `request.state.csp_nonce`; middleware sets `Content-Security-Policy: script-src 'nonce-{nonce}' 'strict-dynamic'; ...`
- Templates use `<script nonce="{{ csp_nonce }}">` — inline scripts without the nonce are blocked by the browser

### 7.9 File Upload Security
- `BaseMediaStorage.validate(file)` reads first 512 bytes and checks against magic-byte signatures for allowed MIME types
- Extension whitelist: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.svg`, `.pdf`
- SVG files: stripped of `<script>` and event attributes before storage
- Files stored in `MEDIA_ROOT` outside the web-served directory tree; served via a dedicated route that sets `Content-Disposition: attachment` for non-image types
- Filenames sanitised: unicode normalised, spaces to hyphens, non-alphanumeric characters removed, length capped at 200 characters

### 7.10 CSV Formula Injection Neutralisation
- Any cell value beginning with `=`, `+`, `-`, or `@` is prefixed with a tab character before writing to CSV
- Applied in a single `sanitise_csv_cell()` utility function called from all export paths

### 7.11 Constraint Error Mapping
- `SQLiteRepository` catches `sqlite3.IntegrityError` and maps it to `ConflictError` (→ HTTP 409) with a generic message (`"A record with this identifier already exists."`)
- The original SQLite error message (which may contain table/column names) is logged at DEBUG level only — never returned to the client

### 7.12 Honeypot
- All public forms include a hidden `<input name="website">` field styled `display:none`
- Controller checks: if `website` field is non-empty, request is silently discarded (returns 200 to avoid tipping off bots)

---

## 8. Test Architecture

### 8.1 Overview

| Metric | Value |
|--------|-------|
| Test files | 15 |
| Total tests | ~69 |
| Test type | End-to-end (real HTTP stack) |
| HTTP client | `httpx` `TestClient` (sync wrapper around ASGI) |
| Database | Real SQLite, in-memory or temp-file per test session |
| External services | None (mailer and storage mocked via container override) |

### 8.2 Test Infrastructure

`tests/conftest.py` provides:

- `app` fixture: calls `bootstrap.py` with test environment variables (`APP_ENV=test`, `DB_PATH=:memory:`).
- `client` fixture: wraps `app` in `httpx.Client` with base URL; handles cookie jar across requests.
- `authed_client` fixture: calls `client`, logs in as a superadmin test user, returns cookie-carrying client.
- `db` fixture: returns the `BaseDatabaseManager` instance from the test container for direct assertion queries.
- Mailer and storage mocked by re-registering a `RecordingMailer` and `InMemoryStorage` as singletons before `mount()`.

### 8.3 Test File Mapping

| File | Coverage Area |
|------|--------------|
| `test_public_pages.py` | Home, about, sitemap, RSS, slug resolution, 404 |
| `test_search.py` | FTS5 search, empty query, injection characters |
| `test_contact.py` | Form validation, honeypot, rate limit, inbox delivery |
| `test_auth.py` | Login, logout, wrong password, locked account, token revocation |
| `test_totp.py` | Enrollment, verification, backup codes, reset |
| `test_password_change.py` | Forced change, gate enforcement, change flow |
| `test_users.py` | CRUD, role assignment, soft-delete |
| `test_content.py` | CRUD, slug uniqueness (409), bulk actions, draft autosave |
| `test_media.py` | Upload, magic-byte rejection, SVG sanitisation, download |
| `test_redirects.py` | Rule CRUD, 301/302 behaviour, public resolution |
| `test_settings.py` | Read, write, cache invalidation |
| `test_audit_log.py` | Event capture, log viewer, retention cleanup |
| `test_scheduler.py` | Job registration, manual trigger, retry, failure event |
| `test_health.py` | `/api/health` response structure, subsystem checks |
| `test_rbac.py` | Role boundaries — viewer cannot mutate, editor scope |

### 8.4 Test Conventions

- Each test is fully self-contained; no shared mutable state between tests.
- Database is reset per test function via transaction rollback in the `db` fixture teardown.
- Tests assert on HTTP status codes, response JSON/HTML content, and direct DB state via the `db` fixture.
- No mocking of internal service or repository calls — tests exercise the full stack.
- Scheduler jobs are not running during tests; triggered explicitly via the manual-trigger endpoint.

---

## 9. Configuration Reference

All runtime configuration is read from environment variables at startup in `bootstrap.py`. No configuration files are read at runtime (only at build time for Docker image).

```
APP_MODULES          = public,admin,scheduler   # modules to activate
APP_ENV              = production               # development | production
APP_DEBUG            = false                    # verbose logging
DB_PATH              = /data/app.db             # SQLite file path
SECRET_KEY           = <≥32 random bytes>       # HMAC signing key
SECURITY_SECRET_KEY  = <≥32 random bytes>       # secondary security key
MEDIA_ROOT           = /data/media              # upload storage root
BACKUP_DIR           = /data/backups            # DB backup destination
PLUGIN_MODULES       =                          # comma-separated dotted paths
```

`bootstrap.py` validates that `SECRET_KEY` and `SECURITY_SECRET_KEY` are present and at least 32 bytes when `APP_ENV=production`. Missing or short secrets cause immediate startup failure with a descriptive error message.
