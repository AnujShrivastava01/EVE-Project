import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DiagnosticTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None


class CentreOffering(BaseModel):
    centre_id: uuid.UUID
    centre_name: str
    price: Decimal
    is_available: bool


class DiagnosticTestDetail(DiagnosticTestRead):
    offered_at: list[CentreOffering]
