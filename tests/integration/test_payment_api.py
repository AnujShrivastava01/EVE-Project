import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Payment
from tests.factories import book, pay

PAYMENTS = "/api/v1/payments/"


@pytest.fixture
def booking(client: TestClient, auth_headers: dict, catalogue: SimpleNamespace) -> dict:
    return book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()


def booking_status(client: TestClient, headers: dict, booking_id: str) -> str:
    return client.get(f"/api/v1/bookings/{booking_id}", headers=headers).json()["status"]


class TestSimulatedPayment:
    def test_successful_payment_confirms_booking(
        self, client: TestClient, auth_headers: dict, booking: dict
    ) -> None:
        response = pay(client, auth_headers, booking["id"], "success")
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "SUCCESS"
        assert body["booking_status"] == "CONFIRMED"
        assert body["amount"] == booking["amount"] == "350.00"
        assert body["provider_payment_id"].startswith("pay_")
        assert booking_status(client, auth_headers, booking["id"]) == "CONFIRMED"

    def test_failed_payment_marks_booking_failed(
        self, client: TestClient, auth_headers: dict, booking: dict
    ) -> None:
        response = pay(client, auth_headers, booking["id"], "failure")
        assert response.status_code == 201  # request processed; the *payment* failed
        assert response.json()["status"] == "FAILED"
        assert response.json()["failure_reason"]
        assert booking_status(client, auth_headers, booking["id"]) == "FAILED"

    def test_server_default_outcome_when_client_does_not_choose(
        self, client: TestClient, auth_headers: dict, booking: dict, monkeypatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "payment_simulation_mode", "failure")
        assert pay(client, auth_headers, booking["id"]).json()["status"] == "FAILED"

    def test_client_cannot_supply_amount(
        self, client: TestClient, auth_headers: dict, booking: dict
    ) -> None:
        response = client.post(
            PAYMENTS, headers=auth_headers, json={"booking_id": booking["id"], "amount": "1.00"}
        )
        assert response.json()["amount"] == "350.00"

    def test_override_can_be_disabled(
        self, client: TestClient, auth_headers: dict, booking: dict, monkeypatch
    ) -> None:
        monkeypatch.setattr(get_settings(), "payment_allow_simulation_override", False)
        response = pay(client, auth_headers, booking["id"], "failure")
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "SIMULATION_OVERRIDE_DISABLED",
        )

    def test_requires_authentication(self, client: TestClient, booking: dict) -> None:
        assert client.post(PAYMENTS, json={"booking_id": booking["id"]}).status_code == 401

    def test_unknown_booking_and_bad_input(self, client: TestClient, auth_headers: dict) -> None:
        missing = pay(client, auth_headers, uuid.uuid4())
        assert (missing.status_code, missing.json()["error"]["code"]) == (404, "BOOKING_NOT_FOUND")
        assert (
            client.post(PAYMENTS, headers=auth_headers, json={"booking_id": "x"}).status_code == 422
        )
        assert client.post(PAYMENTS, headers=auth_headers, json={}).status_code == 422
        bad_outcome = pay(client, auth_headers, uuid.uuid4(), "maybe")
        assert bad_outcome.status_code == 422

    def test_cannot_pay_for_another_users_booking(
        self,
        client: TestClient,
        other_headers: dict,
        auth_headers: dict,
        booking: dict,
        db: Session,
    ) -> None:
        response = pay(client, other_headers, booking["id"], "success")
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "BOOKING_ACCESS_DENIED",
        )
        assert db.scalar(select(func.count()).select_from(Payment)) == 0
        assert booking_status(client, auth_headers, booking["id"]) == "PENDING"

    def test_duplicate_payment_attempts(
        self, client: TestClient, auth_headers: dict, booking: dict, db: Session
    ) -> None:
        assert pay(client, auth_headers, booking["id"], "success").status_code == 201
        second = pay(client, auth_headers, booking["id"], "success")
        assert (second.status_code, second.json()["error"]["code"]) == (409, "BOOKING_NOT_PAYABLE")
        assert db.scalar(select(func.count()).select_from(Payment)) == 1

    def test_second_attempt_while_first_is_pending(
        self, client: TestClient, auth_headers: dict, booking: dict
    ) -> None:
        assert pay(client, auth_headers, booking["id"], "pending").status_code == 201
        second = pay(client, auth_headers, booking["id"], "success")
        assert (second.status_code, second.json()["error"]["code"]) == (
            409,
            "PAYMENT_ALREADY_EXISTS",
        )

    def test_cannot_pay_for_cancelled_booking(
        self, client: TestClient, auth_headers: dict, booking: dict
    ) -> None:
        client.post(f"/api/v1/bookings/{booking['id']}/cancel", headers=auth_headers)
        response = pay(client, auth_headers, booking["id"], "success")
        assert response.status_code == 409
