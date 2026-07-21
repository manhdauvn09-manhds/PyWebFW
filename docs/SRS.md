# Software Requirements Specification — PyWebFW v0.2.0

## 1. Project Overview

PyWebFW is an OOP-first Python web framework built on FastAPI and SQLite. It provides a production-ready foundation for content-driven websites with a public-facing site, a CMS back-office, a built-in task scheduler, and an analytics layer — all composed from swappable, interface-backed modules.

**Version:** 0.2.0  
**Stack:** Python 3.11+, FastAPI, SQLite (FTS5), Jinja2, httpx (test client)  
**Editions:** Community (open-core) and Pro (plugin extensions)

---

## 2. Stakeholders and User Roles

| Role | Description | Typical Permissions |
|------|-------------|---------------------|
| Superadmin | System owner, created at bootstrap | All permissions including user management and system settings |
| Admin | Trusted operator | Content, media, redirects, backup, scheduler, audit logs |
| Editor | Content author | Create/edit/publish own content, upload media |
| Viewer | Read-only staff | View content and analytics inside admin, no mutations |
| Anonymous Visitor | Public internet user | Read published pages, submit contact form, use search |

Role permissions are stored in the database and enforced at the service layer via RBAC middleware. No hard-coded role checks in route handlers.

---

## 3. Functional Requirements

### 3.1 Public Module

#### 3.1.1 Home Page
- Display featured/pinned content items ordered by publish date.
- Render configurable hero banner from site settings.
- Cache rendered HTML in memory; invalidate on content publish/update.

#### 3.1.2 About Page
- Static CMS-managed page rendered from a well-known slug (`about`).
- Supports rich-text body stored as HTML in database.

#### 3.1.3 Contact Page
- Render contact form (name, email, subject, message, honeypot field).
- Validate inputs server-side; reject honeypot-populated submissions silently.
- Rate-limit submissions per IP (configurable window and max count).
- Persist valid submissions to `contact_inbox` table; trigger email notification if mailer configured.

#### 3.1.4 Full-Text Search
- Use SQLite FTS5 virtual table (`content_fts`) over title and body columns.
- Accept `q` query parameter; quote all tokens before passing to FTS5 to prevent injection.
- Return paginated results with highlighted snippets via `snippet()` FTS5 function.
- Return empty result set (not 500) for empty or whitespace-only queries.

#### 3.1.5 Sitemap
- Serve `/sitemap.xml` listing all published pages with `<lastmod>` and `<changefreq>`.
- Exclude draft, archived, and password-protected pages.
- Cache output with `Cache-Control: public, max-age=3600`.

#### 3.1.6 RSS Feed
- Serve `/feed.rss` (RSS 2.0) with latest N published items (configurable, default 20).
- Include title, link, description (truncated body), `pubDate`, and GUID.

#### 3.1.7 Dynamic Slug Pages
- Route `/{slug}` resolves to a published content item or a redirect rule.
- Return 404 for unpublished or non-existent slugs.
- Check redirect rules table before 404; issue 301 or 302 per rule configuration.
- Support nested slugs (`/category/slug`) via prefix matching.

#### 3.1.8 Traffic Analytics
- Record page view events (path, referrer, user-agent, IP hash, timestamp) in `analytics_events` table.
- Aggregate views per page per day in a summary table updated by scheduler job.
- Expose read-only analytics dashboard to Admin/Superadmin roles only.

---

### 3.2 Admin Module

#### 3.2.1 Authentication
- Login form accepts username and password.
- Passwords hashed with PBKDF2-HMAC-SHA256, 310 000 iterations.
- Session token issued as HMAC-signed opaque string; stored in `HttpOnly; Secure; SameSite=Strict` cookie.
- Token includes `token_version` field; incrementing version in DB revokes all existing tokens for that user.
- Failed login attempts tracked per IP; account locked after configurable threshold (default 5 attempts / 15-minute window).

#### 3.2.2 Forced Password Change on First Login
- New accounts flagged `must_change_password = true`.
- After successful login with this flag set, redirect to password-change screen.
- All other admin routes return 403 until password is changed.

#### 3.2.3 TOTP Two-Factor Authentication
- Users may enroll a TOTP authenticator (RFC 6238, SHA-1, 30 s step, 6 digits).
- On login, if TOTP enrolled, require OTP code after password validation.
- Provide 8 single-use backup codes generated at enrollment; codes stored as individual PBKDF2 hashes.
- Superadmin can reset TOTP for any user.

#### 3.2.4 User CRUD
- Superadmin: create, read, update, soft-delete users.
- Assign roles from predefined list (superadmin, admin, editor, viewer).
- User list paginated; filterable by role and active status.
- Deleting a user sets `is_active = false`; preserves audit history.

#### 3.2.5 Content CRUD
- Create, edit, publish, unpublish, archive content items.
- Fields: title, slug (auto-generated, editable), body (rich text), excerpt, featured image, category, tags, status, publish date, author.
- Slug uniqueness enforced at DB level; constraint error returned as 409 (no internal schema exposed).
- Draft autosave every 30 seconds via AJAX endpoint.
- Bulk actions: publish, unpublish, delete selected items.

#### 3.2.6 Menu Management
- Define hierarchical navigation menus (up to 3 levels deep).
- Menu items link to internal slugs, external URLs, or content categories.
- Drag-and-drop order stored as integer position field.
- Multiple named menus (e.g., `primary`, `footer`).

#### 3.2.7 Contact Inbox
- List contact form submissions; mark as read/unread, archive, delete.
- Full submission detail view with reply-by-email link.
- Filter by status (new, read, archived) and date range.

#### 3.2.8 Audit Logs
- Immutable append-only log of all admin mutations (who, what, when, before/after values for key fields).
- Displayed in reverse-chronological order; filterable by user and action type.
- Retention controlled by `LogCleanup` scheduler job (configurable days, default 90).

#### 3.2.9 Site Settings
- Key-value store for global settings: site name, tagline, logo, contact email, social links, analytics toggle, maintenance mode flag, items-per-page, etc.
- Changes applied immediately; no restart required.
- Settings cached in memory; cache invalidated on write.

#### 3.2.10 Media Upload
- Upload images and documents through admin UI.
- Server validates MIME type against whitelist (JPEG, PNG, GIF, WebP, SVG, PDF) by inspecting file magic bytes, not only extension.
- Files stored under a configurable `MEDIA_ROOT`; served via `/media/` route with `Content-Disposition` header.
- Media library grid view with search by filename and filter by type.
- Duplicate filename handling: append timestamp suffix.

#### 3.2.11 Redirect Rules
- Define source path → destination URL redirects with 301 or 302 status.
- Rules evaluated before slug resolution in public router.
- CRUD interface; active/inactive toggle.

#### 3.2.12 Database Backup
- Trigger on-demand SQLite backup via admin UI (uses SQLite online backup API).
- List existing backups with timestamp, size, download link.
- Superadmin-only. Backup files stored in `BACKUP_DIR`; not served publicly.
- Scheduler can run automated backups on a configurable cron schedule.

#### 3.2.13 Scheduler Job Monitoring
- Admin dashboard lists all registered scheduler jobs with last run time, next run time, last status (OK/FAIL), and last error message.
- Manual trigger button executes a job immediately (async, result polled via status endpoint).
- Job run history stored in `scheduler_runs` table; viewable per job.

#### 3.2.14 System Health
- `/admin/health` page aggregates subsystem health: DB connectivity, disk usage, memory usage, scheduler loop status, cache hit rate, SNS channel delivery status.
- Response structure: `{ status: Healthy|Warning|Critical, checks: [...] }`.
- Also exposed as JSON at `/api/health` for external monitoring probes.

---

### 3.3 Scheduler Module

#### 3.3.1 Built-in Jobs

| Job | Default Interval | Description |
|-----|-----------------|-------------|
| `ServerHealthCheck` | 5 min | Check CPU, memory, disk; emit `ServerHealthChecked` event |
| `DatabaseHealthCheck` | 5 min | Run `PRAGMA integrity_check`; emit `DatabaseHealthChecked` event |
| `LogCleanup` | Daily 02:00 | Delete audit log entries older than retention window |
| `CacheWarmup` | Daily 06:00 | Pre-render home page and top N pages into memory cache |
| `DatabaseOptimize` | Weekly Sunday 03:00 | Run `PRAGMA optimize` and `ANALYZE` |
| `IdleConnectionCloser` | 10 min | Return idle DB connections to pool |

#### 3.3.2 Manual Trigger
- Any registered job can be triggered manually from admin UI.
- Triggered run recorded in `scheduler_runs` with `trigger = manual`.

#### 3.3.3 Retry Policy
- Failed jobs retry up to 3 times with exponential backoff (base 30 s).
- After exhausting retries, emit `JobFailed` event on EventBus (admin notification).
- Final failure state persisted in `scheduler_runs`.

#### 3.3.4 Job Registration
- Jobs are registered at startup by implementing `BaseSchedulerJob` ABC and declaring `job_id`, `schedule` (cron expression or interval), and `run()` coroutine.
- Pro edition adds additional jobs via plugin registration.

---

## 4. Non-Functional Requirements

### 4.1 Security
- Passwords: PBKDF2-HMAC-SHA256, 310 000 iterations, per-user salt.
- Session tokens: HMAC-signed, versioned for server-side revocation.
- CSRF protection: `SameSite=Strict` cookie + double-submit token for forms.
- CSP: per-request nonce injected into script/style tags; `Content-Security-Policy` header set by middleware.
- Rate limiting: login attempts per IP; contact form submissions per IP.
- RBAC: role and permission records fetched from DB; checked in service layer before any mutation.
- SQL: 100% parameterised queries; no string interpolation in SQL.
- FTS5: user query tokens individually quoted before FTS5 evaluation.
- XSS: Jinja2 auto-escaping enabled globally; raw HTML only in whitelisted rich-text fields rendered with `| safe` only after server-side sanitisation.
- Upload security: magic-byte MIME validation; no executable extensions; files stored outside web root.
- CSV export: formula-injection neutralisation (prefix `=`, `+`, `-`, `@` with tab character).
- Constraint errors mapped to 409 responses without exposing schema details.
- Honeypot field on all public forms.

### 4.2 Performance
- SQLite connection pool (configurable pool size, default 5).
- In-memory LRU cache for rendered pages (configurable max entries and TTL).
- FTS5 for full-text search — sub-millisecond queries at typical content scale.
- Static assets served with `Cache-Control: public, max-age=31536000` and content-hash filenames.
- `gzip` response compression via middleware for text responses above 1 KB.

### 4.3 Reliability
- Scheduler runs in an independent asyncio task; failure does not crash the HTTP server.
- DB operations wrapped in explicit transactions; rollback on exception.
- Litestream continuous replication available in Pro edition (RPO ~1 s).
- R2-compatible object storage backup destination available in Pro edition.

### 4.4 Observability
- Structured JSON access log to stdout (path, method, status, duration_ms, user_id).
- EventBus events carry correlation IDs; subscribers can forward to external sinks.
- `/api/health` JSON endpoint for uptime monitors.

### 4.5 Deployability
- Single Docker image; runtime behaviour selected by `APP_MODULES` environment variable.
- `APP_MODULES=public,admin` — full site with CMS.
- `APP_MODULES=public` — read-only public site (admin routes not mounted).
- `APP_MODULES=scheduler` — background worker only (no HTTP routes).
- `APP_MODULES=public,admin,scheduler` — all-in-one (default for single-server deploy).
- No external services required for Community edition (SQLite + local disk).

---

## 5. Module-Based Deployment Modes

| Mode | Modules | Use Case |
|------|---------|----------|
| All-in-one | public, admin, scheduler | Single VPS / development |
| Split web+worker | public+admin / scheduler | Separate web and worker containers |
| Read-only CDN origin | public | Edge-cached read replica |
| Worker only | scheduler | Cron replacement in multi-instance setup |

---

## 6. Plugin System

Third-party and Pro features are delivered as `AppModule` implementations:

- `AppModule` is an ABC with `mount(app: FastAPI, container: ServiceContainer) -> None`.
- Plugins declare their routes, services, and middleware within `mount()`.
- Plugins are loaded at startup from paths listed in `PLUGIN_MODULES` environment variable.
- Plugins may register additional scheduler jobs, EventBus subscribers, and admin nav items.
- Community edition ships zero plugins; Pro edition ships bundled plugins (analytics, R2 backup, Litestream, extended media).

---

## 7. Environment Variables (Reference)

| Variable | Purpose |
|----------|---------|
| `APP_MODULES` | Comma-separated list of modules to activate |
| `APP_ENV` | Runtime environment (`development`, `production`) |
| `APP_DEBUG` | Enable debug mode and verbose logging (`true`/`false`) |
| `DB_PATH` | Filesystem path to SQLite database file |
| `SECRET_KEY` | Master secret for HMAC token signing |
| `SECURITY_SECRET_KEY` | Secondary secret for security-sensitive operations |

No default values for secrets are provided. Application refuses to start if `SECRET_KEY` is missing or shorter than 32 bytes in production mode.

---

## 8. Constraints and Assumptions

- Python 3.11 or later required.
- SQLite 3.35+ required (for FTS5, `RETURNING` clause, and `PRAGMA optimize`).
- No JavaScript framework dependency in public templates; vanilla JS only for admin interactivity.
- All admin UI accessible without JavaScript disabled for core CRUD flows; JS enhances but does not gate.
- Media storage is local filesystem in Community edition; S3-compatible storage is a Pro plugin swap.
