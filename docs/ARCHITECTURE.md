# Architecture

## Style

Layered + Clean Architecture flavor, dependencies always point inward:

```
HTTP (FastAPI/Starlette)
   │
Controllers (web pages / API) ──► Services (business rules, audit, cache)
                                      │
                                  Repositories (SQL, mapping)
                                      │
                                  BaseDatabaseManager (pool, transactions)
Scheduler Engine ──► Jobs ──► Services / Managers (same stack, no HTTP)
```

- **Domain** (`app/domain`) knows nothing about HTTP or SQL.
- **Repositories** depend on the `BaseDatabaseManager` ABC, not sqlite3.
- **Services** depend on repositories + cache/security ABCs.
- **Controllers** depend on services only.
- **bootstrap.py** is the single composition root (all concrete wiring).

## Inheritance trees

```
FrameworkError ─► ConfigurationError / DatabaseError / CacheError /
                  ValidationFailedError / AuthenticationError / AuthorizationError /
                  NotFoundError / ConflictError / RateLimitExceededError / SchedulerError

BaseLogger (ABC) ─► StructuredLogger
BaseResponse (ABC) ─► ApiResponse[T]
BaseValidator[T] (ABC) ─► UserInputValidator
BaseDatabaseManager (ABC) ─► SQLiteDatabaseManager          (PostgresManager later)
BaseCacheManager (ABC) ─► InMemoryCacheManager              (RedisManager later)
BaseAuthHandler (ABC) ─► TokenAuthHandler
AuthGuard ─► RoleGuard                                       (RBAC)
BaseHealthChecker ─► ServerHealthChecker / DatabaseHealthChecker

BaseEntity ─► User / MenuItem / AuditLog / ContentItem / DbConnectionProfile
BaseRepository[T] (ABC) ─► UserRepository / MenuRepository / LogRepository /
                           ContentRepository / DbConnectionRepository
BaseService ─► AuthService / UserService / MenuService / ContentService /
               SearchService / DashboardService / SystemService
               (mutating services also mix in AuditMixin)

UiComponent (ABC) ─► CompositeComponent / SeoMeta / HeaderComponent /
                     NavigationComponent / BreadcrumbsComponent / FooterComponent /
                     TableComponent / FormComponent / SearchFormWidget / StatCardWidget
BaseLayout (ABC) ─► PublicLayout / AdminLayout
BasePage (ABC) ─► PublicPage ─► HomePage / SearchPage / SitemapPage /
                                ContentPage ─► AboutPage / ContactPage /
                                               IntroductionPage / PrivacyPolicyPage /
                                               TermsPage / EditorialPolicyPage
              ─► AdminPage ─► AdminHomePage / DashboardPage / UserManagementPage /
                              MenuManagementPage / LogManagementPage /
                              DbConnectionManagementPage
              ─► AdminLoginPage

BaseController (ABC) ─► PublicWebController / AdminWebController
                     ─► BaseApiController ─► PublicApiController
                                          ─► AdminApiController ─► AdminAuthApiController /
                                             AdminUserApiController / AdminMenuApiController /
                                             AdminLogApiController / AdminDashboardApiController /
                                             AdminSystemApiController

Schedule (ABC) ─► IntervalSchedule / DailyTimeSchedule
BaseSchedulerJob (ABC) ─► ServerHealthCheckJob / DatabaseHealthCheckJob /
                          LogCleanupJob / CacheWarmupJob / DatabaseOptimizeJob /
                          IdleConnectionCloserJob
```

## Design patterns

| Pattern | Where | Why |
|---|---|---|
| Template Method | `BasePage.render`, `BaseValidator.validate`, `BaseSchedulerJob.execute`, `BaseController.build_router`, `BaseLayout.render` | fixed pipeline, customizable steps |
| Repository | `BaseRepository[T]` + children | isolate SQL/mapping from business logic |
| Unit of Work | `UnitOfWork` + ambient `contextvars` transaction | atomic multi-repository writes |
| Strategy | `Schedule` (interval vs daily), `BaseCacheManager`, `BaseDatabaseManager` | swappable behavior |
| Dependency Injection | `ServiceContainer` + constructor injection | low coupling, testability |
| Factory | `SettingsFactory`, `LoggerFactory`, page factories in web controllers | centralized creation |
| Composite | `UiComponent` / `CompositeComponent` | nested UI rendering |
| Object Pool | `ConnectionPool` | bounded DB connections + idle close policy |
| Registry | `JobRegistry`, controller list in bootstrap | extensibility |
| Chain of Responsibility | middleware stack | cross-cutting HTTP concerns |
| Mixin | `AuditMixin` | composition over inheritance for audit trail |

## Security model

- PBKDF2-HMAC-SHA256 password hashing (salted, 310k iterations), constant-time compare.
- HMAC-signed expiring tokens; admin pages use HttpOnly + SameSite=Strict cookie
  (CSRF mitigation), API clients use `Authorization: Bearer`.
- RBAC via `RoleGuard`; admin endpoints require the `admin` role.
- All SQL parameterized; ORDER BY columns whitelisted per repository.
- All template output escaped (`esc()` / `xml_escape`) — XSS-safe by construction.
- Identical error for wrong user/password (no enumeration); sanitized error
  envelope (no stack traces); rate-limited API; security headers middleware;
  admin pages `noindex, nofollow`.
