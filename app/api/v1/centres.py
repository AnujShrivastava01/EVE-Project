import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbSession
from app.schemas.centre import CentreRead, CentreTestRead
from app.schemas.error import error_responses
from app.services.centre_service import CentreService
from app.utils.pagination import Page, Pagination

router = APIRouter(prefix="/centres", tags=["centres"])

Search = Annotated[
    str | None,
    Query(max_length=100, description="Case-insensitive substring match"),
]


@router.get(
    "",
    response_model=Page[CentreRead],
    summary="List diagnostic centres",
    description="Public. Results are ordered by name and cached in Redis.",
    responses=error_responses(422),
)
def list_centres(
    db: DbSession,
    cache: CacheDep,
    page: Pagination,
    search: Search = None,
    include_inactive: Annotated[bool, Query(description="Also list closed centres")] = False,
) -> Page[CentreRead]:
    return CentreService(db, cache).list_centres(
        search=search, include_inactive=include_inactive, page=page
    )


@router.get(
    "/{centre_id}",
    response_model=CentreRead,
    summary="Get one centre",
    responses=error_responses(404, 422),
)
def get_centre(centre_id: uuid.UUID, db: DbSession, cache: CacheDep) -> CentreRead:
    return CentreService(db, cache).get_centre(centre_id)


@router.get(
    "/{centre_id}/tests",
    response_model=Page[CentreTestRead],
    summary="Tests offered by a centre, with prices",
    responses=error_responses(404, 422),
)
def list_centre_tests(
    centre_id: uuid.UUID,
    db: DbSession,
    cache: CacheDep,
    page: Pagination,
    search: Search = None,
    only_available: Annotated[bool, Query(description="Hide unavailable tests")] = False,
) -> Page[CentreTestRead]:
    return CentreService(db, cache).list_centre_tests(
        centre_id, search=search, only_available=only_available, page=page
    )
