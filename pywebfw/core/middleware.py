"""HTTP middleware (Chain of Responsibility via Starlette's middleware stack)."""
from __future__ import annotations

import asyncio
import secrets
import time

from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from pywebfw.core.logging import LoggerFactory
from pywebfw.core.responses import ApiResponse
from pywebfw.core.security import SlidingWindowRateLimiter
from pywebfw.services.site_settings_service import SiteSettingsService


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Structured access log with latency for every request."""

    def __init__(self, app) -> None:
        super().__init__(app)
        self._logger = LoggerFactory.get("http")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        self._logger.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client-IP sliding window applied to API routes, plus a much
    stricter dedicated window for the login endpoint (anti brute-force).

    Note: the client IP comes from `request.client`, which uvicorn rewrites
    from X-Forwarded-For when run with --proxy-headers (see Dockerfile)."""

    def __init__(
        self,
        app,
        limiter: SlidingWindowRateLimiter,
        login_limiter: SlidingWindowRateLimiter | None = None,
        login_path: str = "/api/admin/auth/login",
        protect_prefix: str = "/api/",
    ) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._login_limiter = login_limiter
        self._login_path = login_path
        self._prefix = protect_prefix

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        client_ip = request.client.host if request.client else "unknown"
        if (self._login_limiter is not None and request.method == "POST"
                and path == self._login_path):
            if not self._login_limiter.allow(client_ip):
                return JSONResponse(
                    status_code=429,
                    content=ApiResponse.fail(
                        "RATE_LIMITED", "Too many login attempts, try again later").to_dict(),
                )
        if path.startswith(self._prefix):
            if not self._limiter.allow(client_ip):
                return JSONResponse(
                    status_code=429,
                    content=ApiResponse.fail("RATE_LIMITED", "Too many requests").to_dict(),
                )
        return await call_next(request)


class MaintenanceMiddleware(BaseHTTPMiddleware):
    """When the maintenance_mode setting is on, the public site answers 503;
    the admin area and health probes stay reachable so it can be turned off."""

    _EXEMPT_PREFIXES = ("/admin", "/api/admin", "/healthz")

    def __init__(self, app, site_settings: SiteSettingsService) -> None:
        super().__init__(app)
        self._settings = site_settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith(self._EXEMPT_PREFIXES):
            # Read-through cache (30s TTL) — no DB hit on the hot path.
            if await asyncio.to_thread(self._settings.is_maintenance):
                from pywebfw.web.error_pages import render_error_page
                nonce = getattr(request.state, "csp_nonce", "")
                return HTMLResponse(
                    render_error_page(503, "We are doing scheduled maintenance. "
                                      "Please check back shortly.",
                                      nonce=nonce, show_home_link=False),
                    status_code=503,
                    headers={"Retry-After": "300"},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline security headers + per-request CSP nonce.

    A fresh nonce is generated for every request and exposed via
    `request.state.csp_nonce`; layouts/pages stamp it onto their inline
    <style>/<script> blocks, so the CSP can ban all other inline code."""

    # Swagger UI (debug only) loads its assets from a CDN — exempt it.
    _CSP_EXEMPT_PREFIXES = ("/api/docs", "/api/redoc", "/openapi.json")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        if not request.url.path.startswith(self._CSP_EXEMPT_PREFIXES):
            response.headers.setdefault(
                "Content-Security-Policy",
                f"default-src 'self'; script-src 'nonce-{nonce}'; "
                f"style-src 'nonce-{nonce}'; img-src 'self' data:; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            )
        return response
