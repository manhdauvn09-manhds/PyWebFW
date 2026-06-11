"""Standalone styled error pages (404/500/503...).

Deliberately self-contained: no menus, no services, no DB access — an error
page must never trigger further errors. The CSP nonce keeps the inline style
allowed under the strict Content-Security-Policy.
"""
from __future__ import annotations

from app.web.components import esc

_ERROR_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#f1f5f9;color:#1f2937;
display:flex;align-items:center;justify-content:center;min-height:100vh}
.error-box{text-align:center;padding:3rem;background:#fff;border:1px solid #e2e8f0;
border-radius:14px;box-shadow:0 4px 16px rgba(15,23,42,.08);max-width:480px}
.error-code{font-size:4.5rem;font-weight:800;color:#2563eb;margin:0;line-height:1}
.error-message{color:#6b7280;margin:1rem 0 1.5rem}
a.home-link{display:inline-block;padding:.55rem 1.4rem;background:#2563eb;color:#fff;
border-radius:8px;text-decoration:none}
"""

_TITLES = {
    404: "Page not found",
    403: "Access denied",
    429: "Too many requests",
    500: "Something went wrong",
    503: "Temporarily unavailable",
}


def render_error_page(status_code: int, message: str = "", *, nonce: str = "",
                      show_home_link: bool = True) -> str:
    title = _TITLES.get(status_code, "Error")
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    home = '<a class="home-link" href="/">Back to home</a>' if show_home_link else ""
    detail = f'<p class="error-message">{esc(message)}</p>' if message else ""
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{status_code} — {esc(title)}</title>"
        '<meta name="robots" content="noindex">'
        f"<style{nonce_attr}>{_ERROR_CSS}</style></head><body>"
        f'<div class="error-box"><p class="error-code">{status_code}</p>'
        f"<h1>{esc(title)}</h1>{detail}{home}</div></body></html>"
    )
