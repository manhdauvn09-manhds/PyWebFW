"""Reusable UI components (Composite pattern).

Every visual element is a `UiComponent` that renders itself to HTML.
All dynamic values pass through `esc()` — XSS-safe by construction.
"""
from __future__ import annotations

import html
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Sequence

from app.domain.models import MenuItem


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


class UiComponent(ABC):
    @abstractmethod
    def render(self) -> str: ...


class CompositeComponent(UiComponent):
    """A component made of child components."""

    def __init__(self, children: Sequence[UiComponent] = ()) -> None:
        self._children: list[UiComponent] = list(children)

    def add(self, child: UiComponent) -> "CompositeComponent":
        self._children.append(child)
        return self

    def render(self) -> str:
        return "\n".join(child.render() for child in self._children)


@dataclass(frozen=True, slots=True)
class SeoMeta(UiComponent):
    """SEO metadata block rendered into <head>."""

    title: str
    description: str = ""
    canonical: str = ""
    robots: str = "index, follow"

    def render(self) -> str:
        parts = [f"<title>{esc(self.title)}</title>"]
        if self.description:
            parts.append(f'<meta name="description" content="{esc(self.description)}">')
        parts.append(f'<meta name="robots" content="{esc(self.robots)}">')
        if self.canonical:
            parts.append(f'<link rel="canonical" href="{esc(self.canonical)}">')
        parts.append(f'<meta property="og:title" content="{esc(self.title)}">')
        return "\n".join(parts)


class HeaderComponent(UiComponent):
    def __init__(self, site_name: str, tagline: str = "") -> None:
        self._site_name = site_name
        self._tagline = tagline

    def render(self) -> str:
        tagline = f'<small>{esc(self._tagline)}</small>' if self._tagline else ""
        return (f'<header class="site-header"><a class="brand" href="/">'
                f'{esc(self._site_name)}</a> {tagline}</header>')


class NavigationComponent(UiComponent):
    def __init__(self, items: Sequence[MenuItem], active_url: str = "") -> None:
        self._items = items
        self._active_url = active_url

    def render(self) -> str:
        links = []
        for item in self._items:
            cls = ' class="active"' if item.url == self._active_url else ""
            links.append(f'<a href="{esc(item.url)}"{cls}>{esc(item.title)}</a>')
        return f'<nav class="site-nav">{" | ".join(links)}</nav>'


class BreadcrumbsComponent(UiComponent):
    def __init__(self, crumbs: Sequence[tuple[str, str]]) -> None:
        self._crumbs = crumbs  # (label, url); last url may be ""

    def render(self) -> str:
        if not self._crumbs:
            return ""
        parts = []
        for label, url in self._crumbs:
            parts.append(f'<a href="{esc(url)}">{esc(label)}</a>' if url else esc(label))
        return f'<div class="breadcrumbs">{" &rsaquo; ".join(parts)}</div>'


class FooterComponent(UiComponent):
    def __init__(self, site_name: str, links: Sequence[tuple[str, str]] = ()) -> None:
        self._site_name = site_name
        self._links = links

    def render(self) -> str:
        links = " | ".join(f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in self._links)
        return (f'<footer class="site-footer">{links}'
                f'<div>&copy; {esc(self._site_name)}. All rights reserved.</div></footer>')


class TableComponent(UiComponent):
    """Generic data table used by admin management screens."""

    def __init__(self, headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
        self._headers = headers
        self._rows = rows

    def render(self) -> str:
        head = "".join(f"<th>{esc(h)}</th>" for h in self._headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{esc(cell)}</td>" for cell in row) + "</tr>"
            for row in self._rows
        ) or f'<tr><td colspan="{len(self._headers)}">No data</td></tr>'
        return f'<table class="data-table"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


@dataclass(frozen=True, slots=True)
class FormField:
    name: str
    label: str
    input_type: str = "text"
    required: bool = True


class FormComponent(UiComponent):
    """Declarative form; admin forms submit JSON to the admin API via fetch."""

    def __init__(self, form_id: str, action: str, fields: Sequence[FormField],
                 submit_label: str = "Save") -> None:
        self._id = form_id
        self._action = action
        self._fields = fields
        self._submit = submit_label

    def render(self) -> str:
        inputs = "".join(
            f'<label>{esc(f.label)}<input type="{esc(f.input_type)}" name="{esc(f.name)}"'
            f'{" required" if f.required else ""}></label>'
            for f in self._fields
        )
        return (
            f'<form id="{esc(self._id)}" data-action="{esc(self._action)}" class="app-form">'
            f'{inputs}<button type="submit">{esc(self._submit)}</button>'
            f'<div class="form-message" role="alert"></div></form>'
        )


class SearchFormWidget(UiComponent):
    def __init__(self, query: str = "", action: str = "/search") -> None:
        self._query = query
        self._action = action

    def render(self) -> str:
        return (f'<form class="search-form" method="get" action="{esc(self._action)}">'
                f'<input type="search" name="q" value="{esc(self._query)}" '
                f'placeholder="Search..." minlength="2"><button>Search</button></form>')


class PaginationComponent(UiComponent):
    """Prev/Next pager via the `?page=` query parameter."""

    def __init__(self, page: int, pages: int, base_path: str) -> None:
        self._page = page
        self._pages = pages
        self._base = base_path

    def render(self) -> str:
        if self._pages <= 1:
            return ""
        parts = []
        if self._page > 1:
            parts.append(f'<a href="{esc(self._base)}?page={self._page - 1}">&larr; Prev</a>')
        parts.append(f"<span>Page {self._page} / {self._pages}</span>")
        if self._page < self._pages:
            parts.append(f'<a href="{esc(self._base)}?page={self._page + 1}">Next &rarr;</a>')
        return f'<nav class="pagination">{" ".join(parts)}</nav>'


class BarChartComponent(UiComponent):
    """Server-rendered SVG bar chart — no JS, CSP-friendly."""

    def __init__(self, series: Sequence[tuple[str, int]], *, title: str = "",
                 width: int = 640, height: int = 180) -> None:
        self._series = list(series)
        self._title = title
        self._width = width
        self._height = height

    def render(self) -> str:
        if not self._series:
            return f"<p>{esc(self._title)}: no data yet</p>"
        peak = max(value for _, value in self._series) or 1
        plot_h = self._height - 40
        bar_zone = self._width / len(self._series)
        bar_w = min(48, bar_zone * 0.6)
        bars = []
        for index, (label, value) in enumerate(self._series):
            bar_h = max(2, round(plot_h * value / peak))
            x = round(index * bar_zone + (bar_zone - bar_w) / 2)
            y = plot_h - bar_h + 16
            cx = round(x + bar_w / 2)
            bars.append(
                f'<rect x="{x}" y="{y}" width="{round(bar_w)}" height="{bar_h}"'
                ' rx="3" fill="#2563eb"></rect>'
                f'<text x="{cx}" y="{y - 4}" text-anchor="middle"'
                f' font-size="11">{esc(value)}</text>'
                f'<text x="{cx}" y="{plot_h + 32}" text-anchor="middle"'
                f' font-size="10" fill="#6b7280">{esc(label)}</text>'
            )
        title = (f'<h3>{esc(self._title)}</h3>' if self._title else "")
        return (f'{title}<svg viewBox="0 0 {self._width} {self._height}"'
                f' width="{self._width}" height="{self._height}"'
                ' role="img" xmlns="http://www.w3.org/2000/svg">'
                + "".join(bars) + "</svg>")


class StatCardWidget(UiComponent):
    def __init__(self, label: str, value: object) -> None:
        self._label = label
        self._value = value

    def render(self) -> str:
        return (f'<div class="stat-card"><div class="stat-value">{esc(self._value)}</div>'
                f'<div class="stat-label">{esc(self._label)}</div></div>')
