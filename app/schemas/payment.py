import enum
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import BookingStatus, PaymentStatus


class SimulatedOutcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PENDING = "pending"  # leave the payment open; the provider webhook settles it later


class PaymentCreate(BaseModel):
    """The amount is NOT accepted from the client: it always comes from the booking."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"booking_id": "6c1d4a52-1b8e-4f0e-9a52-0a6f4f0b7d21"}}
    )

    booking_id: uuid.UUID
    simulate_outcome: SimulatedOutcome | None = Field(
        default=None,
        description=(
            "Demo/test hook to force the simulator's result. Only honoured when "
            "PAYMENT_ALLOW_SIMULATION_OVERRIDE=true; otherwise the server-side default applies."
        ),
    )


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    booking_id: uuid.UUID
    provider_payment_id: str = Field(description="Id the (simulated) provider uses in webhooks")
    amount: Decimal
    status: PaymentStatus
    failure_reason: str | None
    booking_status: BookingStatus
    created_at: datetime
