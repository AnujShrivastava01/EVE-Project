import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CentreRead(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "3f2b1c0e-8a54-4c4e-9f0a-6f1f6d9c2a11",
                "name": "CityCare Diagnostics",
                "location": "12 MG Road, Bengaluru",
                "is_active": True,
                "created_at": "2026-01-01T09:00:00Z",
            }
        },
    )

    id: uuid.UUID
    name: str
    location: str
    is_active: bool
    created_at: datetime


class CentreTestRead(BaseModel):
    """A test as offered by one centre (with that centre's price)."""

    test_id: uuid.UUID
    name: str
    description: str | None
    price: Decimal = Field(description="Price at this centre (decimal string, 2 places)")
    is_available: bool
