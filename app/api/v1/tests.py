import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession
from app.schemas.error import error_responses
from app.schemas.test import DiagnosticTestDetail, DiagnosticTestRead
from app.services.centre_service import CentreService
from app.utils.pagination import Page, Pagination

router = APIRouter(prefix="/tests", tags=["tests"])


@router.get(
    "",
    response_model=Page[DiagnosticTestRead],
    summary="List the diagnostic test catalogue",
    responses=error_responses(422),
)
def list_tests(
    db: DbSession,
    page: Pagination,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> Page[DiagnosticTestRead]:
    return CentreService(db).list_tests(search=search, page=page)


@router.get(
    "/{test_id}",
    response_model=DiagnosticTestDetail,
    summary="One test and every active centre that offers it (cheapest first)",
    responses=error_responses(404, 422),
)
def get_test(test_id: uuid.UUID, db: DbSession) -> DiagnosticTestDetail:
    return CentreService(db).get_test(test_id)
