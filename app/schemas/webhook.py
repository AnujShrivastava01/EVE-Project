import enum
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WebhookEventType(enum.StrEnum):
    PAYMENT_SUCCESS = "payment.success"
    PAYMENT_FAILED = "payment.failed"


_EXPECTED_STATUS = {
    WebhookEventType.PAYMENT_SUCCESS: "SUCCESS",
    WebhookEventType.PAYMENT_FAILED: "FAILED",
}


class WebhookPayload(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "event_id": "evt_123",
                "event_type": "payment.success",
                "payment_id": "pay_0123456789abcdef01234567",
                "booking_id": "6c1d4a52-1b8e-4f0e-9a52-0a6f4f0b7d21",
                "status": "SUCCESS",
            }
        }
    )

    event_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:\-]+$")
    event_type: WebhookEventType
    payment_id: str = Field(min_length=1, max_length=64, description="provider_payment_id")
    booking_id: uuid.UUID
    status: Literal["SUCCESS", "FAILED"]

    @model_validator(mode="after")
    def _event_type_matches_status(self) -> "WebhookPayload":
        if _EXPECTED_STATUS[self.event_type] != self.status:
            raise ValueError(f"event_type {self.event_type.value} is inconsistent with status")
        return self


class WebhookOutcome(enum.StrEnum):
    PROCESSED = "processed"  # state applied (or was already in the requested state)
    DUPLICATE = "duplicate"  # event_id seen before; nothing done
    IGNORED = "ignored"  # valid but not applicable (e.g. conflicting late event); recorded only
    QUEUED = "queued"  # transient failure; a Celery task will retry


class WebhookResponse(BaseModel):
    event_id: str
    result: WebhookOutcome
