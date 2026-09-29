import enum
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models.base import Base, UUIDPrimaryKeyMixin


class WebhookEventStatus(enum.StrEnum):
    # Claimed by the in-flight processing transaction. A committed row is never in this state:
    # if processing fails the transaction rolls back and the claim disappears with it.
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"  # applied to payment/booking
    IGNORED = "IGNORED"  # valid, recorded, but no state change was applicable (late/conflicting)
    FAILED = "FAILED"  # gave up after retries; kept for manual inspection/replay


class WebhookEvent(UUIDPrimaryKeyMixin, Base):
    """One row per provider event_id ever seen.

    The UNIQUE constraint on event_id is the idempotency guarantee: INSERT ... ON CONFLICT DO
    NOTHING lets exactly one of N (possibly concurrent) deliveries proceed.
    """

    __tablename__ = "webhook_events"

    event_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookEventStatus] = mapped_column(
        Enum(
            WebhookEventStatus, native_enum=False, length=20, create_constraint=True, name="status"
        ),
        nullable=False,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
