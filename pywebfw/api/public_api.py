"""Public JSON API consumed by the public front-end."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, EmailStr, Field

from pywebfw.core.pagination import PageRequest
from pywebfw.core.routing import BaseApiController
from pywebfw.domain.models import MenuArea
from pywebfw.services.contact_service import ContactInput, ContactService
from pywebfw.services.content_service import ContentService
from pywebfw.services.menu_service import MenuService
from pywebfw.services.search_service import SearchService


class ContactSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    subject: str = Field("", max_length=150)
    message: str = Field(min_length=10, max_length=5000)
    website: str = Field("", max_length=200)   # honeypot — humans leave it empty


class PublicApiController(BaseApiController):
    prefix = "/api/public"
    tags = ["public-api"]

    def __init__(self, menus: MenuService, contents: ContentService,
                 search: SearchService, contact: ContactService) -> None:
        self._menus = menus
        self._contents = contents
        self._search = search
        self._contact = contact

    def _register(self, router: APIRouter) -> None:
        @router.get("/menus")
        def get_menus() -> dict:
            items = self._menus.get_menu(MenuArea.PUBLIC)
            return self.ok([item.to_dict() for item in items])

        @router.get("/content/{slug}")
        def get_content(slug: str) -> dict:
            return self.ok(self._contents.get_page(slug).to_dict())

        @router.get("/search")
        def search(
            q: str = Query(min_length=2, max_length=100),
            page: int = Query(1, ge=1),
            size: int = Query(10, ge=1, le=50),
        ) -> dict:
            result = self._search.search(q, PageRequest.create(page=page, size=size))
            return self.paginated(result, lambda item: item.to_dict())

        @router.get("/sitemap")
        def sitemap() -> dict:
            return self.ok(self._contents.sitemap_entries())

        @router.post("/contact", status_code=201)
        def submit_contact(payload: ContactSubmission, request: Request) -> dict:
            ip = request.client.host if request.client else "unknown"
            ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16]
            self._contact.submit(
                ContactInput(name=payload.name, email=str(payload.email),
                             subject=payload.subject, message=payload.message,
                             honeypot=payload.website),
                ip_hash=ip_hash,
            )
            # Honeypot submissions get the same answer — bots learn nothing.
            return self.ok({"received": True})
