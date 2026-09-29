"""Small helpers shared by the tests."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import Booking, User
from app.schemas.booking import BookingCreate
from app.services.booking_service import BookingService

PASSWORD = "Passw0rdX1"


def future(days: int = 2, hours: int = 0) -> datetime:
    return datetime.now(UTC) + timedelta(days=days, hours=hours)


def signup_and_login(client: TestClient, email: str, name: str = "Test User") -> dict[str, str]:
    signup = client.post(
        "/api/v1/auth/signup", json={"name": name, "email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, signup.text
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def create_user(db: Session, email: str | None = None, *, is_active: bool = True) -> User:
    user = User(
        name="Service Test",
        email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password(PASSWORD),
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    return user


def create_booking_via_service(
    db: Session,
    user: User,
    catalogue: SimpleNamespace,
    *,
    days: int = 2,
    when: datetime | None = None,
) -> Booking:
    return BookingService(db).create_booking(
        user,
        BookingCreate(
            centre_id=catalogue.alpha.id,
            test_id=catalogue.cbc.id,
            appointment_at=when or future(days),
        ),
    )


def book(
    client: TestClient,
    headers: dict[str, str],
    centre_id: object,
    test_id: object,
    when: datetime | None = None,
    **extra: object,
):
    return client.post(
        "/api/v1/bookings",
        headers=headers,
        json={
            "centre_id": str(centre_id),
            "test_id": str(test_id),
            "appointment_at": (when or future()).isoformat(),
            **extra,
        },
    )


def pay(
    client: TestClient, headers: dict[str, str], booking_id: object, outcome: str | None = None
):
    body: dict[str, object] = {"booking_id": str(booking_id)}
    if outcome:
        body["simulate_outcome"] = outcome
    return client.post("/api/v1/payments/", headers=headers, json=body)


def webhook_body(payment: dict, event_id: str = "evt_1", status: str = "SUCCESS") -> dict:
    return {
        "event_id": event_id,
        "event_type": "payment.success" if status == "SUCCESS" else "payment.failed",
        "payment_id": payment["provider_payment_id"],
        "booking_id": payment["booking_id"],
        "status": status,
    }
