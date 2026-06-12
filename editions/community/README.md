# PyWebFW — OOP-first Python Web Framework (Community Edition)

A reusable, layered, object-oriented web framework: public site + CMS,
admin area, public/admin APIs, scheduler, revocable-token auth, plugin
system — built on FastAPI/Starlette, everything above transport level is
framework-owned OOP code.

> **License**: source-available — free for personal projects, evaluation and
> internal use. Commercial production use and redistribution require a
> commercial license. See [LICENSE](LICENSE).

## Community vs Pro

| Community (this repo) | **PyWebFW Pro** (commercial) |
|---|---|
| Full CMS: contents + form editor, FTS5 search, dynamic routes, menus | **Scale**: PostgreSQL driver, Redis cache (multi-server) |
| Admin: users (form editor), logs, dashboard, settings + maintenance mode, jobs monitor | **Security**: 2FA TOTP, Session Manager (revoke-everywhere) |
| Contact form (honeypot + throttle) + admin inbox | **Insight**: traffic analytics + dashboard charts |
| Auth: PBKDF2, revocable tokens, forced first-login password change, RBAC, login throttle | **Ops**: Backup Manager, CSV export |
| Scheduler engine + 6 jobs, event bus, plugin system (`AppModule`) | **SEO/Content**: Redirect Manager (auto-301 on slug change), Media Manager |
| Docker split deployment + Caddy HTTPS + deploy scripts, CLI scaffolding | Priority support & custom modules |

**Get Pro / commercial license:** open an issue on this repo or contact the author.

## Quick start

```bash
pip install -e .
pywebfw new mysite          # scaffold a complete project
cd mysite && python run.py
```

- Public site: http://127.0.0.1:8000/ — Admin: http://127.0.0.1:8000/admin
  (first login `admin / ChangeMe!123`, a password change is enforced)

## Extend through plugins (never edit the framework)

```python
from pywebfw.bootstrap import ApplicationBuilder
from mysite.extensions import ProjectModule

app = ApplicationBuilder(plugins=[ProjectModule()]).build_app()
```

`AppModule` hooks: `controllers()` (pages/APIs), `jobs()`, `register_services()`,
`subscribe_events()`, `init_schema()`.

## Deployment

One Docker image, role per container via `APP_MODULES`
(public / admin / scheduler), automatic HTTPS via the Caddy profile:

```powershell
.\deploy\deploy-all.ps1 -Server <ip> -User deploy -Domain example.com
```

See `docs/` for the full documentation suite (architecture, class design,
SRS, guideline, user manual). Some documented modules belong to Pro.

## Tests

```bash
pip install pytest httpx
pytest -q
```
