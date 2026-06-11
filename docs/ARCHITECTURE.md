# Architecture (summary)

> Full documentation with diagrams: open [docs/index.html](index.html)
> (Architecture Design, Class Design, SRS, Guideline, User Manual).

## Style

Layered + Clean Architecture; dependencies always point inward and stop at ABCs:

```
HTTP (FastAPI/Starlette)
   │  middleware: security headers+CSP nonce → rate limit (+login throttle)
   │              → logging → maintenance → traffic tracking
Controllers (web pages / API) ──► Services (business rules, audit, cache, events)
                                      │
                                  Repositories (parameterized SQL, mapping)
                                      │
                                  PooledDatabaseManager (SQLite | Postgres)
Scheduler Engine ──► Jobs ──► Services / Managers (same stack, no HTTP)
EventBus: services publish facts; bootstrap wires subscribers
bootstrap.py = the single composition root
```

## Key contracts (ABC → implementations)

| Contract | Implementations |
|---|---|
| `BaseDatabaseManager` → `PooledDatabaseManager` | `SQLiteDatabaseManager` (default), `PostgresDatabaseManager` (`DB_DRIVER=postgres`) |
| `BaseCacheManager` | `InMemoryCacheManager` (default), `RedisCacheManager` (`CACHE_BACKEND=redis`) |
| `BaseAuthHandler` | `TokenAuthHandler` (revocable tokens via `token_version`, role from DB, 2FA flag) |
| `BaseMailer` | `SmtpMailer`, `NullMailer` (default without `MAIL_HOST`) |
| `BaseMediaStorage` | `LocalMediaStorage` |
| `BaseSchedulerJob` + `Schedule` | 8 jobs: health ×2, cleanup, cache warmup, traffic flush, optimize, backup, idle-close |
| `BaseRepository[T]` | User, Menu, Log, Content, DbConnection, Setting, Contact, Redirect (+ Traffic standalone) |
| `BasePage` → Public/Admin | 10 public pages + dynamic `/{slug}` catch-all; 14 admin screens |
| `BaseController` → `BaseApiController` | Public API + 11 admin API controllers (RBAC via `AdminApiController`) |

## Deployment

One Docker image; `APP_MODULES` (public/admin/scheduler) selects the role per
container. Caddy profile terminates TLS (auto Let's Encrypt + HSTS). App ports
bind 127.0.0.1 only. PowerShell deploy scripts (local/remote SSH) in `deploy/`.

## Security model (highlights)

PBKDF2-310k passwords · revocable HMAC tokens (logout/password change kill all
sessions) · forced first-login password change · 2FA TOTP · RBAC with DB-fresh
roles · login throttle 5/5min/IP · parameterized SQL + ORDER BY whitelists ·
FTS5 query quoting · XSS escape by construction + per-request CSP nonce ·
HttpOnly/SameSite=Strict/Secure cookie · honeypot + throttled contact form ·
upload whitelist + random stored names · CSV formula-injection neutralization ·
sanitized DB errors (constraint race → 409) · audit trail for every mutation.

## Testing

69 end-to-end tests through the real HTTP stack (`pytest -q`), GitHub Actions CI.
