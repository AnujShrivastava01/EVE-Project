import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    BookingAccessDeniedError,
    BookingNotFoundError,
    CentreNotFoundError,
    CentreTestNotFoundError,
    CentreUnavailableError,
    DiagnosticTestNotFoundError,
    DiagnosticTestUnavailableError,
    DuplicateBookingError,
    InvalidAppointmentError,
    PaymentInProgressError,
)
from app.core.logging import bind_log_context
from app.db.models import Booking, BookingStatus, User
from app.repositories.booking_repository import BookingRepository
from app.repositories.centre_repository import CentreRepository
from app.repositories.payment_repository import PaymentRepository
from app.schemas.booking import BookingCreate
from app.utils.pagination import PaginationParams

logger = logging.getLogger(__name__)

# Name of the partial unique index that forbids two live bookings for the same user/test/time.
ACTIVE_SLOT_INDEX = "uq_bookings_active_user_slot"


def assert_booking_owner(booking: Booking | None, user: User) -> Booking:
    """404 if the booking doesn't exist, 403 if it belongs to someone else."""
    if booking is None:
        raise BookingNotFoundError()
    if booking.user_id != user.id:
        raise BookingAccessDeniedError()
    return booking


class BookingService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.bookings = BookingRepository(db)
        self.centres = CentreRepository(db)
        self.payments = PaymentRepository(db)

    # --- create ------------------------------------------------------------------
    def create_booking(self, user: User, data: BookingCreate) -> Booking:
        self._validate_appointment(data.appointment_at)

        centre = self.centres.get_centre(data.centre_id)
        if centre is None:
            raise CentreNotFoundError()
        if not centre.is_active:
            raise CentreUnavailableError()

        centre_test = self.centres.get_centre_test(data.centre_id, data.test_id)
        if centre_test is None:
            # Distinguish "no such test anywhere" from "this centre doesn't offer it".
            if self.centres.get_test(data.test_id) is None:
                raise DiagnosticTestNotFoundError()
            raise CentreTestNotFoundError()
        if not centre_test.is_available:
            raise DiagnosticTestUnavailableError()

        booking = Booking(
            user_id=user.id,
            centre_test_id=centre_test.id,
            appointment_at=data.appointment_at,
            amount=centre_test.price,  # price comes from the DB, never from the client
            status=BookingStatus.PENDING,
        )
        try:
            self.bookings.add(booking)
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                == ACTIVE_SLOT_INDEX
            ):
                raise DuplicateBookingError() from exc
            raise

        bind_log_context(booking_id=str(booking.id))
        logger.info("booking_created", extra={"amount": str(booking.amount)})
        return self._load(booking.id)

    def _validate_appointment(self, appointment_at: datetime) -> None:
        settings = get_settings()
        now = datetime.now(UTC)
        earliest = now + timedelta(minutes=settings.booking_min_lead_minutes)
        latest = now + timedelta(days=settings.booking_max_days_ahead)
        if appointment_at < earliest:
            raise InvalidAppointmentError(
                f"Appointment must be at least {settings.booking_min_lead_minutes} minutes "
                "in the future"
            )
        if appointment_at > latest:
            raise InvalidAppointmentError(
                f"Appointment cannot be more than {settings.booking_max_days_ahead} days ahead"
            )

    # --- read ----------------------------------------------------------------------
    def get_booking(self, user: User, booking_id: uuid.UUID) -> Booking:
        booking = self.bookings.get(booking_id)
        assert_booking_owner(booking, user)
        return booking

    def list_bookings(
        self, user: User, *, status: BookingStatus | None, page: PaginationParams
    ) -> tuple[list[Booking], int]:
        return self.bookings.list_for_user(
            user.id, status=status, limit=page.limit, offset=page.offset
        )

    # --- cancel ----------------------------------------------------------------------
    def cancel_booking(self, user: User, booking_id: uuid.UUID) -> Booking:
        assert_booking_owner(self.bookings.get(booking_id), user)

        # Lock the row so a concurrent payment/webhook can't change the status under us.
        booking = self.bookings.get_for_update(booking_id)
        if booking.status is BookingStatus.PENDING and self.payments.has_pending_for_booking(
            booking.id
        ):
            # A charge is in flight; cancelling now could strand the money. The payment must
            # settle (webhook) or expire (cleanup job) first.
            raise PaymentInProgressError()
        booking.transition_to(BookingStatus.CANCELLED)  # 409 if not allowed
        self.db.commit()

        bind_log_context(booking_id=str(booking.id))
        logger.info("booking_cancelled")
        return self._load(booking.id)

    # --- helpers ---------------------------------------------------------------------
    def _load(self, booking_id: uuid.UUID) -> Booking:
        booking = self.bookings.get(booking_id)
        assert booking is not None
        return booking
