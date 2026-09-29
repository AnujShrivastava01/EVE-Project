import enum
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, Numeric, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.exceptions import AppError
from app.db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.models.booking import Booking


class PaymentStatus(enum.StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


# A payment starts PENDING and settles exactly once. SUCCESS/FAILED are terminal.
ALLOWED_PAYMENT_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({PaymentStatus.SUCCESS, PaymentStatus.FAILED}),
    PaymentStatus.SUCCESS: frozenset(),
    PaymentStatus.FAILED: frozenset(),
}


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
        # At most ONE live payment (PENDING or SUCCESS) per booking, enforced by the database, so
        # two racing payment requests can never both succeed in creating a charge.
        Index(
            "uq_payments_active_per_booking",
            "booking_id",
            unique=True,
            postgresql_where=text("status IN ('PENDING', 'SUCCESS')"),
        ),
        # Used by the stale-PENDING cleanup job.
        Index("ix_payments_status_created_at", "status", "created_at"),
    )

    booking_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("bookings.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, native_enum=False, length=20, create_constraint=True, name="status"),
        nullable=False,
        default=PaymentStatus.PENDING,
        server_default=PaymentStatus.PENDING.value,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(255))

    booking: Mapped[Booking] = relationship()

    def can_transition_to(self, new_status: PaymentStatus) -> bool:
        return new_status in ALLOWED_PAYMENT_TRANSITIONS[self.status]

    def transition_to(self, new_status: PaymentStatus, failure_reason: str | None = None) -> None:
        if not self.can_transition_to(new_status):
            raise AppError(  # programming error: callers check can_transition_to first
                f"Illegal payment transition {self.status.value} -> {new_status.value}"
            )
        self.status = new_status
        if new_status is PaymentStatus.FAILED:
            self.failure_reason = failure_reason
