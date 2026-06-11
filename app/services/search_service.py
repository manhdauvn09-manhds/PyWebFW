"""Public search over published content."""
from __future__ import annotations

from app.core.pagination import PageRequest, PageResult
from app.domain.models import ContentItem
from app.repositories.content_repository import ContentRepository
from app.services.base import BaseService

MIN_QUERY_LENGTH = 2


class SearchService(BaseService):
    def __init__(self, contents: ContentRepository) -> None:
        super().__init__()
        self._contents = contents

    def search(self, query: str, page: PageRequest) -> PageResult[ContentItem]:
        term = query.strip()
        if len(term) < MIN_QUERY_LENGTH:
            return PageResult(items=[], total=0, page=page.page, size=page.size)
        result = self._contents.search(term, page)
        self._logger.info("search executed", query=term, hits=result.total)
        return result
