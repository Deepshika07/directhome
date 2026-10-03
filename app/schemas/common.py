from typing import Any

from pydantic import BaseModel, Field


class PageMeta(BaseModel):
    page: int
    limit: int
    total: int


def ok(data: Any, meta: PageMeta | None = None) -> dict:
    body: dict = {"success": True, "data": data}
    if meta is not None:
        body["meta"] = meta.model_dump()
    return body


def error_body(code: str, message: str) -> dict:
    return {"success": False, "error": {"code": code, "message": message}}


class PageParams:
    """FastAPI dependency: ?page=1&limit=20 (limit capped at 100)."""

    def __init__(self, page: int = 1, limit: int = 20):
        self.page = max(page, 1)
        self.limit = min(max(limit, 1), 100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


class MessageOut(BaseModel):
    message: str = Field(examples=["Listing submitted for review"])
