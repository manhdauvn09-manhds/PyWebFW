from __future__ import annotations

from typing import Any

from app.core.exceptions import DatabaseError
from app.core.pagination import PageRequest, PageResult
from app.domain.models import ContentItem
from app.repositories.base import BaseRepository


class ContentRepository(BaseRepository[ContentItem]):
    @property
    def table_name(self) -> str:
        return "contents"

    @property
    def sortable_columns(self) -> frozenset[str]:
        return frozenset({"id", "slug", "title", "created_at", "updated_at"})

    def _map_row(self, row: dict[str, Any]) -> ContentItem:
        return ContentItem(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            summary=row["summary"],
            body=row["body"],
            seo_title=row["seo_title"],
            seo_description=row["seo_description"],
            is_published=bool(row["is_published"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_params(self, entity: ContentItem) -> dict[str, Any]:
        return {
            "slug": entity.slug,
            "title": entity.title,
            "summary": entity.summary,
            "body": entity.body,
            "seo_title": entity.seo_title,
            "seo_description": entity.seo_description,
            "is_published": int(entity.is_published),
            "created_at": entity.created_at,
            "updated_at": entity.updated_at,
        }

    def find_by_slug(self, slug: str) -> ContentItem | None:
        """Any publish state — used by the admin editor."""
        row = self._db.fetch_one("SELECT * FROM contents WHERE slug = ?", (slug,))
        return self._map_row(row) if row else None

    def slug_exists(self, slug: str, exclude_id: int | None = None) -> bool:
        sql = "SELECT COUNT(*) AS n FROM contents WHERE slug = ?"
        params: list[Any] = [slug]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        row = self._db.fetch_one(sql, params)
        return bool(row and row["n"] > 0)

    def find_published_by_slug(self, slug: str) -> ContentItem | None:
        row = self._db.fetch_one(
            "SELECT * FROM contents WHERE slug = ? AND is_published = 1", (slug,))
        return self._map_row(row) if row else None

    def list_published(self) -> list[ContentItem]:
        rows = self._db.fetch_all(
            "SELECT * FROM contents WHERE is_published = 1 ORDER BY updated_at DESC")
        return [self._map_row(r) for r in rows]

    def search(self, term: str, page: PageRequest) -> PageResult[ContentItem]:
        """Full-text search via SQLite FTS5 (relevance-ranked, prefix match);
        falls back to LIKE on other engines or if FTS5 is unavailable.
        Both paths are parameterized — safe against injection."""
        if self._db.dialect == "sqlite":
            match = self._fts_expression(term)
            if match:
                try:
                    return self._fts_search(match, page)
                except DatabaseError:
                    pass   # FTS5 missing/broken -> degrade gracefully
        pattern = f"%{term}%"
        return self.list_page(
            page,
            where="is_published = 1 AND (title LIKE ? OR summary LIKE ? OR body LIKE ?)",
            params=(pattern, pattern, pattern),
        )

    @staticmethod
    def _fts_expression(term: str) -> str:
        """Builds a safe FTS5 MATCH expression: each token quoted (so user
        input can't inject FTS operators) with prefix matching."""
        tokens = [t.replace('"', "") for t in term.split()]
        return " ".join(f'"{t}"*' for t in tokens if t)

    def _fts_search(self, match: str, page: PageRequest) -> PageResult[ContentItem]:
        base_sql = (" FROM contents c JOIN contents_fts ON contents_fts.rowid = c.id"
                    " WHERE contents_fts MATCH ? AND c.is_published = 1")
        total_row = self._db.fetch_one(f"SELECT COUNT(*) AS n{base_sql}", (match,))
        rows = self._db.fetch_all(
            f"SELECT c.*{base_sql} ORDER BY contents_fts.rank LIMIT ? OFFSET ?",
            (match, page.size, page.offset),
        )
        return PageResult(items=[self._map_row(r) for r in rows],
                          total=total_row["n"] if total_row else 0,
                          page=page.page, size=page.size)
