# PyWebFW — OOP-first Python Web Framework

A reusable, layered, object-oriented framework providing: public front-end
pages, public APIs, an admin area, admin APIs, and a scheduler/cron subsystem —
all sharing one core infrastructure (DI container, DB pool, cache, auth,
logging, validation).

Built on **FastAPI/Starlette** as the HTTP engine; everything above transport
level is framework-owned OOP code.

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
| `app/config/` | Immutable env-based settings (composed dataclasses) |
| `app/core/` | Framework kernel: exceptions, logging, DI container, responses, pagination, validation, security, routing, middleware |
| `app/infrastructure/` | Adapters: database (pool + UoW + schema), cache, auth |
| `app/domain/` | Persistence-agnostic entities (`BaseEntity` + children) |
| `app/repositories/` | `BaseRepository[T]` + one repository per aggregate |
| `app/services/` | Business logic (`BaseService`, `AuditMixin`) |
| `app/web/` | OOP UI: components → layouts → pages (public + admin) + web controllers |
| `app/api/` | Class-based JSON API controllers (public + admin) |
| `app/scheduler/` | Job framework: `Schedule`, `RetryPolicy`, `BaseSchedulerJob`, engine + built-in jobs |
| `app/bootstrap.py` | Composition root — the only place with concrete wiring |
| `tests/` | End-to-end tests through the real HTTP stack |
| `docs/` | Architecture documentation |

## Extending

- **New public page**: subclass `PublicPage` (or `ContentPage` for CMS-backed),
  add one entry in `PublicWebController._page_routes`.
- **New admin screen**: subclass `AdminPage`, add one entry in `AdminWebController`.
- **New API resource**: subclass `BaseApiController` (or `AdminApiController`
  for RBAC-guarded), register it in `ApplicationBuilder._register_controllers`.
- **New scheduled job**: subclass `BaseSchedulerJob`, set `name`/`schedule`/
  `retry_policy`, implement `run()`, register in `ApplicationBuilder._build_scheduler`.
- **New storage engine**: implement `BaseDatabaseManager`; nothing else changes.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.
