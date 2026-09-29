"""Importing this package registers every model on Base.metadata (needed by Alembic)."""

from app.db.models.base import Base
from app.db.models.booking import Booking, BookingStatus
from app.db.models.diagnostic_centre import DiagnosticCentre
from app.db.models.diagnostic_test import CentreTest, DiagnosticTest
from app.db.models.payment import Payment, PaymentStatus
from app.db.models.user import User
from app.db.models.webhook_event import WebhookEvent, WebhookEventStatus

__all__ = [
    "Base",
    "Booking",
    "BookingStatus",
    "CentreTest",
    "DiagnosticCentre",
    "DiagnosticTest",
    "Payment",
    "PaymentStatus",
    "User",
    "WebhookEvent",
    "WebhookEventStatus",
]
