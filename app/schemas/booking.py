import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.models import BookingStatus


class BookingCreate(BaseModel):
    """What the client may choose. There is deliberately no `amount` or `status` field: unknown
    fields are ignored, so a client sending them cannot influence price or state."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "centre_id": "3f2b1c0e-8a54-4c4e-9f0a-6f1f6d9c2a11",
                "test_id": "8a8163a6-7dd8-4011-8d03-05a4d6443bfa",
                "appointment_at": "2030-01-15T09:30:00+05:30",
            }
        }
    )

    centre_id: uuid.UUID
    test_id: uuid.UUID
    appointment_at: datetime = Field(description="Timezone-aware ISO-8601 datetime")

    @field_validator("appointment_at")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("appointment_at must include a timezone offset, e.g. +05:30 or Z")
        return value.astimezone(UTC)


class BookingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    centre_id: uuid.UUID
    centre_name: str
    test_id: uuid.UUID
    test_name: str
    appointment_at: datetime
    amount: Decimal = Field(description="Server-calculated price (decimal string)")
    status: BookingStatus
    created_at: datetime
    updated_at: datetime
