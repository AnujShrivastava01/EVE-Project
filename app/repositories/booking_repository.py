import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Booking, BookingStatus, CentreTest

# Everything BookingRead needs, loaded in one query (avoids N+1 when listing).
_WITH_CENTRE_AND_TEST = (
    joinedload(Booking.centre_test).joinedload(CentreTest.centre),
    joinedload(Booking.centre_test).joinedload(CentreTest.test),
)


class BookingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, booking: Booking) -> Booking:
        self.db.add(booking)
        self.db.flush()  # raises IntegrityError here if a DB constraint is violated
        return booking

    def get(self, booking_id: uuid.UUID) -> Booking | None:
        return self.db.scalar(
            select(Booking)
            .where(Booking.id == booking_id)
            .options(*_WITH_CENTRE_AND_TEST)
            .execution_options(populate_existing=True)
        )

    def get_for_update(self, booking_id: uuid.UUID) -> Booking | None:
        """SELECT ... FOR UPDATE: serialises concurrent state changes on the same booking.

        populate_existing forces the row's *current* values into the identity map; without it a
        booking already loaded earlier in this session could keep stale attributes after we waited
        for another transaction's lock.
        """
        return self.db.scalar(
            select(Booking)
            .where(Booking.id == booking_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def list_for_user(
        self, user_id: uuid.UUID, *, status: BookingStatus | None, limit: int, offset: int
    ) -> tuple[list[Booking], int]:
        query = select(Booking).where(Booking.user_id == user_id)
        if status is not None:
            query = query.where(Booking.status == status)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.scalars(
            query.options(*_WITH_CENTRE_AND_TEST)
            .order_by(Booking.created_at.desc(), Booking.id)
            .limit(limit)
            .offset(offset)
        ).unique()
        return list(rows), total
