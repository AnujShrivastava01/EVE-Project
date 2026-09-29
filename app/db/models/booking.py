import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.exceptions import InvalidBookingTransitionError
from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.diagnostic_test import CentreTest
from app.db.models.user import User


class BookingStatus(enum.StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# The booking state machine. This table is the single source of truth: every status change in the
# code base goes through Booking.transition_to(), which consults it.
#
#   PENDING ──► CONFIRMED ──► CANCELLED
#      ├──────► FAILED
#      └──────► CANCELLED
ALLOWED_BOOKING_TRANSITIONS: dict[BookingStatus, frozenset[BookingStatus]] = {
    BookingStatus.PENDING: frozenset(
        {BookingStatus.CONFIRMED, BookingStatus.FAILED, BookingStatus.CANCELLED}
    ),
    BookingStatus.CONFIRMED: frozenset({BookingStatus.CANCELLED}),
    BookingStatus.FAILED: frozenset(),  # terminal
    BookingStatus.CANCELLED: frozenset(),  # terminal
}

ACTIVE_BOOKING_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


class Booking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        # Listing "my bookings" newest-first.
        Index("ix_bookings_user_id_created_at", "user_id", "created_at"),
        # DB-level guard against double-submits: a user cannot hold two live bookings for the same
        # test at the same centre and time. Cancelled/failed bookings free the slot again.
        Index(
            "uq_bookings_active_user_slot",
            "user_id",
            "centre_test_id",
            "appointment_at",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'CONFIRMED')"),
        ),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    centre_test_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("centre_tests.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    appointment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Snapshot of centre_tests.price at booking time (later price changes don't alter it).
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=20, create_constraint=True, name="status"),
        nullable=False,
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
    )

    user: Mapped[User] = relationship()
    centre_test: Mapped[CentreTest] = relationship()

    # --- read-only conveniences used by the API schema ----------------------
    @property
    def centre_id(self) -> uuid.UUID:
        return self.centre_test.centre_id

    @property
    def test_id(self) -> uuid.UUID:
        return self.centre_test.test_id

    @property
    def centre_name(self) -> str:
        return self.centre_test.centre.name

    @property
    def test_name(self) -> str:
        return self.centre_test.test.name

    # --- state machine -------------------------------------------------------
    def can_transition_to(self, new_status: BookingStatus) -> bool:
        return new_status in ALLOWED_BOOKING_TRANSITIONS[self.status]

    def transition_to(self, new_status: BookingStatus) -> None:
        """The ONLY way to change booking.status. Raises 409 for illegal transitions."""
        if not self.can_transition_to(new_status):
            raise InvalidBookingTransitionError(
                f"Cannot change booking status from {self.status.value} to {new_status.value}",
                details={"from": self.status.value, "to": new_status.value},
            )
        self.status = new_status
