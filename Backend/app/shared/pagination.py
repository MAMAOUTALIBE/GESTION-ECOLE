from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class OffsetParams(BaseModel):
    """Classic page/limit pagination — fine for small/medium lists."""

    page: int = Field(default=1, ge=1)
    pageSize: int = Field(default=25, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.pageSize

    @property
    def limit(self) -> int:
        return self.pageSize


class CursorParams(BaseModel):
    """Cursor pagination — required at the 3M-students scale."""

    cursor: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class OffsetPage(BaseModel, Generic[T]):
    items: list[T]
    page: int
    pageSize: int
    total: int
    totalPages: int


class CursorPage(BaseModel, Generic[T]):
    items: list[T]
    nextCursor: str | None = None
    hasMore: bool = False


def build_offset_page(items: list[T], total: int, params: OffsetParams) -> OffsetPage[T]:
    total_pages = (total + params.pageSize - 1) // params.pageSize if total else 0
    return OffsetPage(
        items=items,
        page=params.page,
        pageSize=params.pageSize,
        total=total,
        totalPages=total_pages,
    )
