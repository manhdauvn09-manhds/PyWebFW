# PyWebFW — OOP-first Python Web Framework

A reusable, layered, object-oriented framework providing: public front-end
pages + CMS, public APIs, an admin area (14 screens), admin APIs, scheduler,
auth with 2FA, traffic analytics, and an event bus — all sharing one core
infrastructure (DI container, DB pool, cache, auth, logging, validation).

Built on **FastAPI/Starlette** as the HTTP engine; everything above transport
level is framework-owned OOP code.

**The framework is an installable package (`pywebfw/`); applications are thin
consumers** — see the demo in `app/` and the scaffolding below.

## Start a new project

```bash
pip install -e .                      # or: pip install pywebfw (when published)
pywebfw new mysite                    # scaffolds a complete project
cd mysite && python run.py            # public site + admin + scheduler running
```

Projects extend the framework through `AppModule` plugins (pages, APIs, jobs,
services, event handlers) — framework code is never modified:

```python
# mysite/main.py
from pywebfw.bootstrap import ApplicationBuilder
from mysite.extensions import ProjectModule

app = ApplicationBuilder(plugins=[ProjectModule()]).build_app()
```

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install fastapi "uvicorn[standard]" "pydantic[email]"
copy .env.example .env          # then edit SECURITY_SECRET_KEY
.\.venv\Scripts\python run.py
```

- Public site: http://127.0.0.1:8000/  (Home, /about, /contact, /search, /sitemap, /rss, ...)
- Admin: http://127.0.0.1:8000/admin  (first-boot login: `admin` / `ChangeMe!123` — change immediately)
- API docs (debug only): http://127.0.0.1:8000/api/docs

Run tests:

```powershell
.\.venv\Scripts\python -m pip install pytest httpx
.\.venv\Scripts\python -m pytest -q
```

## Deployment modes (Docker)

One image, role selected at runtime via `APP_MODULES` — deploy everything on
one server or split FE / Admin / Scheduler across containers:

```powershell
# Split mode: fe (:8001, public only) + admin (:8002) + scheduler (jobs only)
docker compose up -d --build

# Single-server mode: everything in one container (:8000)
docker compose --profile allinone up -d allinone
```

Set `SECURITY_SECRET_KEY` in a `.env` file next to `docker-compose.yml` first.

- `APP_MODULES=public` → public pages + public API only (no admin routes exist).
- `APP_MODULES=admin` → admin pages + admin API only.
- `APP_MODULES=scheduler` → cron jobs only; exposes just `/healthz`.
- Any combination works (`public,admin`, etc.).

**Domain + HTTPS (production):** add the Caddy proxy — automatic Let's Encrypt
certificates, renewal, HTTP→HTTPS redirect and HSTS:

```powershell
# DNS A records for example.com and admin.example.com must point at the server
.\deploy\deploy-all.ps1 -Server <ip> -User deploy -Domain example.com
# → https://example.com (public) + https://admin.example.com (admin)
```

App ports are bound to `127.0.0.1` only; public traffic always goes through
Caddy. The admin cookie is `Secure` in production, and the login endpoint has
its own brute-force limiter (default 5 attempts / 5 minutes / IP).

**Auth hardening:** tokens embed a per-user version — logout and any password
change revoke every outstanding token instantly. The seed admin must change
its password on first login before any other admin action is allowed. Every
HTML response ships a per-request CSP nonce (only framework-stamped inline
style/script may run).

**Scaling backends (same ABCs, zero changes above the infrastructure layer):**

```bash
DB_DRIVER=postgres  DB_DSN=postgresql://user:pw@host/db   # pip install "psycopg[binary]"
CACHE_BACKEND=redis CACHE_REDIS_URL=redis://host:6379/0   # pip install redis
```

Search uses SQLite FTS5 (relevance-ranked, prefix matching, auto-synced by
triggers) and degrades to LIKE automatically if FTS5 is unavailable.

Every mode serves `GET /healthz` for Docker healthchecks / load balancers.
The default SQLite-on-shared-volume setup is single-host; for true multi-server
deployment, implement `BaseDatabaseManager` for Postgres/MySQL — no other layer
changes.

## Layout

| Folder | Purpose |
|---|---|
| `pywebfw/` | **The framework package** (installable, versioned) |
| `pywebfw/config/` | Immutable env-based settings (composed dataclasses) |
| `pywebfw/core/` | Kernel: exceptions, logging, DI container, responses, pagination, validation, security, routing, middleware, events, csv_export |
| `pywebfw/infrastructure/` | Adapters: database (pool + UoW + schema), cache, auth, mail, media |
| `pywebfw/domain/` | Persistence-agnostic entities (`BaseEntity` + children) |
| `pywebfw/repositories/` | `BaseRepository[T]` + one repository per aggregate |
| `pywebfw/services/` | Business logic (`BaseService`, `AuditMixin`) |
| `pywebfw/web/` | OOP UI: components → layouts → pages (public + admin) + web controllers |
| `pywebfw/api/` | Class-based JSON API controllers (public + admin) |
| `pywebfw/scheduler/` | Job framework: `Schedule`, `RetryPolicy`, `BaseSchedulerJob`, engine + built-in jobs |
| `pywebfw/bootstrap.py` | Composition root + `AppModule` plugin hooks |
| `pywebfw/plugins.py` | The plugin contract applications implement |
| `pywebfw/cli.py` | `pywebfw new <project>` scaffolding |
| `app/` | **Demo application** — thin consumer showing the plugin pattern |
| `tests/` | End-to-end tests through the real HTTP stack |
| `docs/` | Architecture documentation |

## Extending (from application code — never edit the framework)

Implement an `AppModule` and pass it to `ApplicationBuilder(plugins=[...])`:

- **`controllers()`** — mount custom pages (subclass `PublicPage`/`AdminPage`)
  and API resources (subclass `BaseApiController`/`AdminApiController`).
- **`jobs()`** — register `BaseSchedulerJob` subclasses.
- **`register_services()`** — add services to the DI container.
- **`subscribe_events()`** — react to domain events (`contact.submitted`,
  `content.slug_changed`, `job.failed`, your own).
- **`init_schema()`** — create the module's tables (idempotent).

New storage/cache/mail backend: implement the matching ABC
(`BaseDatabaseManager`, `BaseCacheManager`, `BaseMailer`) — nothing else changes.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.
