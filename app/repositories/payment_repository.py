import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Payment, PaymentStatus


class PaymentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, payment: Payment) -> Payment:
        self.db.add(payment)
        self.db.flush()
        return payment

    def get(self, payment_id: uuid.UUID) -> Payment | None:
        return self.db.get(Payment, payment_id)

    def get_by_provider_id(self, provider_payment_id: str) -> Payment | None:
        return self.db.scalar(
            select(Payment).where(Payment.provider_payment_id == provider_payment_id)
        )

    def get_for_update(self, payment_id: uuid.UUID) -> Payment | None:
        return self.db.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def list_for_booking(self, booking_id: uuid.UUID) -> list[Payment]:
        return list(
            self.db.scalars(
                select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.created_at)
            )
        )

    def has_pending_for_booking(self, booking_id: uuid.UUID) -> bool:
        return (
            self.db.scalar(
                select(Payment.id)
                .where(Payment.booking_id == booking_id, Payment.status == PaymentStatus.PENDING)
                .limit(1)
            )
            is not None
        )

    def has_active_for_booking(self, booking_id: uuid.UUID) -> bool:
        """PENDING or SUCCESS payment exists (mirrors the partial unique index in the DB)."""
        return (
            self.db.scalar(
                select(Payment.id)
                .where(
                    Payment.booking_id == booking_id,
                    Payment.status.in_([PaymentStatus.PENDING, PaymentStatus.SUCCESS]),
                )
                .limit(1)
            )
            is not None
        )

    def list_stale_pending_ids(self, older_than: datetime, limit: int) -> list[uuid.UUID]:
        return list(
            self.db.scalars(
                select(Payment.id)
                .where(Payment.status == PaymentStatus.PENDING, Payment.created_at < older_than)
                .order_by(Payment.created_at)
                .limit(limit)
            )
        )
