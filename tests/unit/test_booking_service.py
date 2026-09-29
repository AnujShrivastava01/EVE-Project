import itertools
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

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
    InvalidBookingTransitionError,
    PaymentInProgressError,
)
from app.db.models import Booking, BookingStatus, Payment, PaymentStatus
from app.db.models.booking import ALLOWED_BOOKING_TRANSITIONS as TRANSITIONS
from app.schemas.booking import BookingCreate
from app.services.booking_service import BookingService
from app.utils.pagination import PaginationParams
from tests.factories import create_booking_via_service, create_user, future

S = BookingStatus


def _new(status: BookingStatus) -> Booking:
    return Booking(status=status)


ALLOWED = {
    (S.PENDING, S.CONFIRMED),
    (S.PENDING, S.FAILED),
    (S.PENDING, S.CANCELLED),
    (S.CONFIRMED, S.CANCELLED),
}


class TestBookingStateMachine:
    """Pure unit tests: no database needed."""

    @pytest.mark.parametrize(("current", "target"), sorted(ALLOWED))
    def test_allowed_transitions(self, current: BookingStatus, target: BookingStatus) -> None:
        booking = _new(current)
        booking.transition_to(target)
        assert booking.status is target

    @pytest.mark.parametrize(
        ("current", "target"),
        [pair for pair in itertools.product(S, S) if pair not in ALLOWED],
    )
    def test_every_other_transition_is_rejected(
        self, current: BookingStatus, target: BookingStatus
    ) -> None:
        booking = _new(current)
        with pytest.raises(InvalidBookingTransitionError) as exc:
            booking.transition_to(target)
        assert exc.value.status_code == 409
        assert booking.status is current  # unchanged

    def test_terminal_states_have_no_exits(self) -> None:
        assert TRANSITIONS[S.FAILED] == frozenset()
        assert TRANSITIONS[S.CANCELLED] == frozenset()


class TestCreateBooking:
    def test_success_uses_price_from_database(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        booking = create_booking_via_service(db, user, catalogue)
        assert booking.status is BookingStatus.PENDING
        assert booking.amount == Decimal("350.00") == catalogue.alpha_cbc.price
        assert booking.centre_name == "Alpha Diagnostics"
        assert booking.test_name == "CBC"

    def test_amount_is_a_snapshot_of_the_price_at_booking_time(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        booking = create_booking_via_service(db, create_user(db), catalogue)
        catalogue.alpha_cbc.price = Decimal("999.00")
        db.commit()
        db.refresh(booking)
        assert booking.amount == Decimal("350.00")

    def test_unknown_centre(self, db: Session, catalogue: SimpleNamespace) -> None:
        with pytest.raises(CentreNotFoundError):
            BookingService(db).create_booking(
                create_user(db),
                BookingCreate(
                    centre_id=uuid.uuid4(), test_id=catalogue.cbc.id, appointment_at=future()
                ),
            )

    def test_inactive_centre(self, db: Session, catalogue: SimpleNamespace) -> None:
        with pytest.raises(CentreUnavailableError):
            BookingService(db).create_booking(
                create_user(db),
                BookingCreate(
                    centre_id=catalogue.closed.id,
                    test_id=catalogue.cbc.id,
                    appointment_at=future(),
                ),
            )

    def test_test_not_offered_at_centre_vs_test_does_not_exist(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        with pytest.raises(CentreTestNotFoundError):  # Beta doesn't offer HbA1c
            BookingService(db).create_booking(
                user,
                BookingCreate(
                    centre_id=catalogue.beta.id,
                    test_id=catalogue.hba1c.id,
                    appointment_at=future(),
                ),
            )
        with pytest.raises(DiagnosticTestNotFoundError):
            BookingService(db).create_booking(
                user,
                BookingCreate(
                    centre_id=catalogue.alpha.id, test_id=uuid.uuid4(), appointment_at=future()
                ),
            )

    def test_unavailable_test(self, db: Session, catalogue: SimpleNamespace) -> None:
        with pytest.raises(DiagnosticTestUnavailableError):
            BookingService(db).create_booking(
                create_user(db),
                BookingCreate(
                    centre_id=catalogue.alpha.id,
                    test_id=catalogue.lipid.id,
                    appointment_at=future(),
                ),
            )

    @pytest.mark.parametrize("when", [future(days=-1), future(days=0, hours=0), future(days=365)])
    def test_invalid_appointment_times(self, db: Session, catalogue: SimpleNamespace, when) -> None:
        with pytest.raises(InvalidAppointmentError):
            BookingService(db).create_booking(
                create_user(db),
                BookingCreate(
                    centre_id=catalogue.alpha.id, test_id=catalogue.cbc.id, appointment_at=when
                ),
            )

    def test_duplicate_live_booking_blocked_by_database(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        slot = future(days=2)
        first = create_booking_via_service(db, user, catalogue, when=slot)
        with pytest.raises(DuplicateBookingError):
            create_booking_via_service(db, user, catalogue, when=slot)
        # A different user may book the same slot; and once the first booking is cancelled the
        # original user can book it again.
        create_booking_via_service(db, create_user(db), catalogue, when=slot)
        BookingService(db).cancel_booking(user, first.id)
        again = create_booking_via_service(db, user, catalogue, when=slot)
        assert again.status is BookingStatus.PENDING


class TestAccessControl:
    def test_get_other_users_booking_is_forbidden(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        owner, intruder = create_user(db), create_user(db)
        booking = create_booking_via_service(db, owner, catalogue)
        with pytest.raises(BookingAccessDeniedError):
            BookingService(db).get_booking(intruder, booking.id)
        with pytest.raises(BookingAccessDeniedError):
            BookingService(db).cancel_booking(intruder, booking.id)

    def test_missing_booking(self, db: Session) -> None:
        with pytest.raises(BookingNotFoundError):
            BookingService(db).get_booking(create_user(db), uuid.uuid4())

    def test_list_only_returns_own_bookings_newest_first(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        alice, bob = create_user(db), create_user(db)
        first = create_booking_via_service(db, alice, catalogue, days=2)
        second = create_booking_via_service(db, alice, catalogue, days=3)
        create_booking_via_service(db, bob, catalogue, days=2)
        rows, total = BookingService(db).list_bookings(
            alice, status=None, page=PaginationParams(limit=10, offset=0)
        )
        assert total == 2
        assert [b.id for b in rows] == [second.id, first.id]


class TestCancel:
    def test_cancel_pending_then_cancel_again_is_invalid_transition(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        booking = create_booking_via_service(db, user, catalogue)
        assert BookingService(db).cancel_booking(user, booking.id).status is S.CANCELLED
        with pytest.raises(InvalidBookingTransitionError):
            BookingService(db).cancel_booking(user, booking.id)

    def test_cancel_confirmed_allowed_but_failed_is_not(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        confirmed = create_booking_via_service(db, user, catalogue, days=2)
        confirmed.transition_to(S.CONFIRMED)
        failed = create_booking_via_service(db, user, catalogue, days=3)
        failed.transition_to(S.FAILED)
        db.commit()
        assert BookingService(db).cancel_booking(user, confirmed.id).status is S.CANCELLED
        with pytest.raises(InvalidBookingTransitionError):
            BookingService(db).cancel_booking(user, failed.id)

    def test_cannot_cancel_while_payment_in_flight(
        self, db: Session, catalogue: SimpleNamespace
    ) -> None:
        user = create_user(db)
        booking = create_booking_via_service(db, user, catalogue)
        db.add(
            Payment(
                booking_id=booking.id,
                provider_payment_id="pay_inflight",
                amount=booking.amount,
                status=PaymentStatus.PENDING,
            )
        )
        db.commit()
        with pytest.raises(PaymentInProgressError):
            BookingService(db).cancel_booking(user, booking.id)
