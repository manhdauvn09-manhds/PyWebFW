"""PyWebFW — reusable OOP-first Python web framework.

Batteries included: public site + CMS, admin area (users, menus, contents,
media, messages, redirects, backups, settings, sessions, jobs), public/admin
APIs, scheduler, auth (revocable tokens + 2FA), traffic analytics, event bus.

Build an application:

    from pywebfw.bootstrap import ApplicationBuilder
    app = ApplicationBuilder(plugins=[MyModule()]).build_app()

Scaffold a new project:  `python -m pywebfw new myproject`
"""

__version__ = "0.2.0"
