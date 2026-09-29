import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DbSession
from app.db.models import BookingStatus
from app.schemas.booking import BookingCreate, BookingRead
from app.schemas.error import error_responses
from app.services.booking_service import BookingService
from app.utils.pagination import Page, Pagination

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Book a diagnostic test",
    description=(
        "Creates a **PENDING** booking. The amount is looked up server-side from the centre's "
        "price list; any `amount`/`status` sent by the client is ignored."
    ),
    responses=error_responses(400, 401, 404, 409, 422),
)
def create_booking(payload: BookingCreate, user: CurrentUser, db: DbSession) -> BookingRead:
    return BookingRead.model_validate(BookingService(db).create_booking(user, payload))


@router.get(
    "",
    response_model=Page[BookingRead],
    summary="List my bookings (newest first)",
    responses=error_responses(401, 422),
)
def list_bookings(
    user: CurrentUser,
    db: DbSession,
    page: Pagination,
    status_filter: Annotated[
        BookingStatus | None, Query(alias="status", description="Filter by booking status")
    ] = None,
) -> Page[BookingRead]:
    rows, total = BookingService(db).list_bookings(user, status=status_filter, page=page)
    return Page[BookingRead](
        items=[BookingRead.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{booking_id}",
    response_model=BookingRead,
    summary="Get one of my bookings",
    responses=error_responses(401, 403, 404, 422),
)
def get_booking(booking_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BookingRead:
    return BookingRead.model_validate(BookingService(db).get_booking(user, booking_id))


@router.post(
    "/{booking_id}/cancel",
    response_model=BookingRead,
    summary="Cancel a booking",
    description="Allowed from PENDING or CONFIRMED. Blocked while a payment is in flight.",
    responses=error_responses(401, 403, 404, 409, 422),
)
def cancel_booking(booking_id: uuid.UUID, user: CurrentUser, db: DbSession) -> BookingRead:
    return BookingRead.model_validate(BookingService(db).cancel_booking(user, booking_id))
