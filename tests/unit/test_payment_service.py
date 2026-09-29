import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    BookingAccessDeniedError,
    BookingNotFoundError,
    BookingNotPayableError,
    PaymentAlreadyExistsError,
    SimulationOverrideDisabledError,
)
from app.db.database import SessionLocal
from app.db.models import Booking, BookingStatus, Payment, PaymentStatus
from app.schemas.payment import SimulatedOutcome
from app.services.booking_service import BookingService
from app.services.payment_service import PaymentService, decide_outcome
from tests.factories import create_booking_via_service, create_user


@pytest.fixture
def booking(db: Session, catalogue: SimpleNamespace) -> Booking:
    return create_booking_via_service(db, create_user(db), catalogue)


class TestSimulator:
    def test_server_default_is_used_when_client_does_not_choose(self, monkeypatch) -> None:
        for mode, expected in [
            ("success", SimulatedOutcome.SUCCESS),
            ("failure", SimulatedOutcome.FAILURE),
            ("pending", SimulatedOutcome.PENDING),
        ]:
            monkeypatch.setattr(get_settings(), "payment_simulation_mode", mode)
            assert decide_outcome(None) is expected

    def test_client_override_wins_when_allowed(self) -> None:
        assert decide_outcome(SimulatedOutcome.FAILURE) is SimulatedOutcome.FAILURE

    def test_client_override_rejected_when_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "payment_allow_simulation_override", False)
        with pytest.raises(SimulationOverrideDisabledError):
            decide_outcome(SimulatedOutcome.SUCCESS)
        # ...but the server-side default still works.
        assert decide_outcome(None) is SimulatedOutcome.SUCCESS


class TestPay:
    def test_success_confirms_booking_and_uses_booking_amount(
        self, db: Session, booking: Booking
    ) -> None:
        payment = PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)
        assert payment.status is PaymentStatus.SUCCESS
        assert payment.amount == booking.amount
        assert payment.provider_payment_id.startswith("pay_")
        assert payment.booking_status is BookingStatus.CONFIRMED

    def test_failure_fails_booking_and_records_reason(self, db: Session, booking: Booking) -> None:
        payment = PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.FAILURE)
        assert payment.status is PaymentStatus.FAILED
        assert payment.failure_reason
        assert payment.booking_status is BookingStatus.FAILED

    def test_pending_leaves_everything_open_for_the_webhook(
        self, db: Session, booking: Booking
    ) -> None:
        payment = PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.PENDING)
        assert payment.status is PaymentStatus.PENDING
        assert payment.booking_status is BookingStatus.PENDING

    def test_unknown_booking_and_someone_elses_booking(self, db: Session, booking: Booking) -> None:
        with pytest.raises(BookingNotFoundError):
            PaymentService(db).pay(booking.user, uuid.uuid4(), None)
        with pytest.raises(BookingAccessDeniedError):
            PaymentService(db).pay(create_user(db), booking.id, None)
        assert db.scalar(select(func.count()).select_from(Payment)) == 0

    def test_second_payment_after_success_is_rejected(self, db: Session, booking: Booking) -> None:
        PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)
        with pytest.raises(BookingNotPayableError):
            PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)
        assert db.scalar(select(func.count()).select_from(Payment)) == 1

    def test_second_payment_while_first_is_pending_is_rejected(
        self, db: Session, booking: Booking
    ) -> None:
        PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.PENDING)
        with pytest.raises(PaymentAlreadyExistsError):
            PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)

    def test_failed_payment_makes_booking_unpayable(self, db: Session, booking: Booking) -> None:
        PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.FAILURE)
        with pytest.raises(BookingNotPayableError):
            PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)

    def test_cancelled_booking_is_not_payable(self, db: Session, booking: Booking) -> None:
        BookingService(db).cancel_booking(booking.user, booking.id)
        with pytest.raises(BookingNotPayableError):
            PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.SUCCESS)

    def test_database_index_allows_only_one_live_payment_per_booking(
        self, db: Session, booking: Booking
    ) -> None:
        """Even if the service's checks were bypassed, the partial unique index holds."""
        from sqlalchemy.exc import IntegrityError

        for n in range(2):
            db.add(
                Payment(
                    booking_id=booking.id,
                    provider_payment_id=f"pay_raw_{n}",
                    amount=booking.amount,
                    status=PaymentStatus.PENDING,
                )
            )
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.concurrency
def test_concurrent_payments_for_the_same_booking_create_exactly_one_payment(
    db: Session, booking: Booking
) -> None:
    barrier = threading.Barrier(6)
    results: list[str] = []
    lock = threading.Lock()

    def attempt() -> None:
        with SessionLocal() as session:
            user = booking.user
            barrier.wait()
            try:
                PaymentService(session).pay(user, booking.id, SimulatedOutcome.SUCCESS)
                outcome = "paid"
            except (BookingNotPayableError, PaymentAlreadyExistsError) as exc:
                outcome = exc.code
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert results.count("paid") == 1
    assert len(results) == 6
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    db.expire_all()
    assert db.get(Booking, booking.id).status is BookingStatus.CONFIRMED


class TestStalePaymentCleanup:
    def _pending_payment(self, db: Session, booking: Booking, age_minutes: int) -> Payment:
        payment = PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.PENDING)
        db.execute(
            text("UPDATE payments SET created_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(minutes=age_minutes), "id": payment.id},
        )
        db.commit()
        return payment

    def test_old_pending_payment_is_failed_and_booking_failed(
        self, db: Session, booking: Booking
    ) -> None:
        payment = self._pending_payment(db, booking, age_minutes=60)
        assert PaymentService(db).expire_stale_payments() == 1
        db.refresh(payment)
        db.refresh(booking)
        assert payment.status is PaymentStatus.FAILED
        assert booking.status is BookingStatus.FAILED

    def test_recent_pending_payment_is_left_alone(self, db: Session, booking: Booking) -> None:
        payment = self._pending_payment(db, booking, age_minutes=1)
        assert PaymentService(db).expire_stale_payments() == 0
        db.refresh(payment)
        assert payment.status is PaymentStatus.PENDING
