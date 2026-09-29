import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    BookingNotPayableError,
    PaymentAlreadyExistsError,
    SimulationOverrideDisabledError,
)
from app.core.logging import bind_log_context
from app.db.models import BookingStatus, Payment, PaymentStatus, User
from app.repositories.booking_repository import BookingRepository
from app.repositories.payment_repository import PaymentRepository
from app.schemas.payment import SimulatedOutcome
from app.services.booking_service import assert_booking_owner

logger = logging.getLogger(__name__)

SIMULATED_FAILURE_REASON = "Simulated payment failure"
STALE_PAYMENT_REASON = "Payment timed out waiting for the provider"


def decide_outcome(requested: SimulatedOutcome | None) -> SimulatedOutcome:
    """The payment simulator. Deterministic: no randomness anywhere.

    * If the caller asks for an outcome (and the environment allows it) that outcome is used.
    * Otherwise the server default (PAYMENT_SIMULATION_MODE) applies.
    """
    settings = get_settings()
    if requested is not None:
        if not settings.payment_allow_simulation_override:
            raise SimulationOverrideDisabledError()
        return requested
    return SimulatedOutcome(settings.payment_simulation_mode)


class PaymentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.bookings = BookingRepository(db)
        self.payments = PaymentRepository(db)

    def pay(
        self, user: User, booking_id: uuid.UUID, requested_outcome: SimulatedOutcome | None
    ) -> Payment:
        assert_booking_owner(self.bookings.get(booking_id), user)  # 404 / 403
        outcome = decide_outcome(requested_outcome)

        # Lock the booking row: a second concurrent payment request for the same booking waits
        # here, then sees the first one's result and is rejected below.
        booking = self.bookings.get_for_update(booking_id)
        bind_log_context(booking_id=str(booking.id))
        if booking.status is not BookingStatus.PENDING:
            raise BookingNotPayableError(
                f"Booking is {booking.status.value}; only PENDING bookings can be paid"
            )
        if self.payments.has_active_for_booking(booking.id):
            raise PaymentAlreadyExistsError()

        payment = Payment(
            booking_id=booking.id,
            provider_payment_id=f"pay_{uuid.uuid4().hex[:24]}",
            amount=booking.amount,  # always the server-side booking amount
            status=PaymentStatus.PENDING,
        )
        try:
            self.payments.add(payment)
        except IntegrityError as exc:
            # Backstop: the partial unique index uq_payments_active_per_booking fired.
            self.db.rollback()
            raise PaymentAlreadyExistsError() from exc

        if outcome is SimulatedOutcome.SUCCESS:
            payment.transition_to(PaymentStatus.SUCCESS)
            booking.transition_to(BookingStatus.CONFIRMED)
        elif outcome is SimulatedOutcome.FAILURE:
            payment.transition_to(PaymentStatus.FAILED, SIMULATED_FAILURE_REASON)
            booking.transition_to(BookingStatus.FAILED)
        # PENDING: leave both as-is; the webhook (or the stale-payment cleanup) settles it.
        self.db.commit()

        bind_log_context(payment_id=payment.provider_payment_id)
        logger.info(
            "payment_processed",
            extra={"payment_status": payment.status.value, "booking_status": booking.status.value},
        )
        return payment

    def expire_stale_payments(self, batch_size: int = 100) -> int:
        """Fail PENDING payments that never received a webhook (called by a periodic Celery task).

        Frees the booking to end in FAILED instead of hanging in PENDING forever.
        """
        cutoff = datetime.now(UTC) - timedelta(minutes=get_settings().payment_stale_after_minutes)
        expired = 0
        for payment_id in self.payments.list_stale_pending_ids(cutoff, batch_size):
            stale = self.payments.get(payment_id)
            # Same lock order as everywhere else: booking first, then payment.
            booking = self.bookings.get_for_update(stale.booking_id)
            payment = self.payments.get_for_update(payment_id)
            if payment.status is not PaymentStatus.PENDING:  # settled while we were looking
                self.db.rollback()
                continue
            payment.transition_to(PaymentStatus.FAILED, STALE_PAYMENT_REASON)
            if booking.can_transition_to(BookingStatus.FAILED):
                booking.transition_to(BookingStatus.FAILED)
            self.db.commit()
            expired += 1
            logger.warning(
                "stale_payment_expired",
                extra={"payment_id": payment.provider_payment_id, "booking_id": str(booking.id)},
            )
        return expired
