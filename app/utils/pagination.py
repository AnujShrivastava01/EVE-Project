"""limit/offset pagination shared by all list endpoints."""

from typing import Annotated

from fastapi import Depends, Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


class PaginationParams(BaseModel):
    limit: int
    offset: int


def get_pagination(
    limit: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"Page size (max {MAX_PAGE_SIZE})")
    ] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, description="Number of items to skip")] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


Pagination = Annotated[PaginationParams, Depends(get_pagination)]


class Page[T](BaseModel):
    """Envelope for every list response."""

    items: list[T]
    total: int = Field(description="Total number of items matching the filters")
    limit: int
    offset: int
