import json
import threading
from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.deps import get_webhook_service
from app.core.config import get_settings
from app.core.security import sign_webhook_body
from app.db.database import SessionLocal
from app.db.models import Payment, WebhookEvent
from app.main import app
from app.services.webhook_service import WebhookService
from tests.factories import book, pay, webhook_body

WEBHOOK = "/api/v1/payments/webhook/"


@pytest.fixture
def pending(client: TestClient, auth_headers: dict, catalogue: SimpleNamespace) -> dict:
    """A booking with a PENDING payment, waiting for the provider webhook."""
    booking = book(client, auth_headers, catalogue.alpha.id, catalogue.cbc.id).json()
    payment = pay(client, auth_headers, booking["id"], "pending").json()
    return payment


def booking_status(client: TestClient, headers: dict, booking_id: str) -> str:
    return client.get(f"/api/v1/bookings/{booking_id}", headers=headers).json()["status"]


class TestWebhook:
    def test_success_event_confirms_booking(
        self, client: TestClient, auth_headers: dict, pending: dict
    ) -> None:
        response = client.post(WEBHOOK, json=webhook_body(pending))
        assert response.status_code == 200
        assert response.json() == {"event_id": "evt_1", "result": "processed"}
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"

    def test_failed_event_fails_booking(
        self, client: TestClient, auth_headers: dict, pending: dict
    ) -> None:
        response = client.post(WEBHOOK, json=webhook_body(pending, status="FAILED"))
        assert response.json()["result"] == "processed"
        assert booking_status(client, auth_headers, pending["booking_id"]) == "FAILED"

    def test_duplicate_webhook_is_idempotent(
        self, client: TestClient, auth_headers: dict, pending: dict, db: Session
    ) -> None:
        first = client.post(WEBHOOK, json=webhook_body(pending))
        second = client.post(WEBHOOK, json=webhook_body(pending))
        assert (first.status_code, first.json()["result"]) == (200, "processed")
        assert (second.status_code, second.json()["result"]) == (200, "duplicate")
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"

    def test_same_webhook_ten_times_leaves_state_correct(
        self, client: TestClient, auth_headers: dict, pending: dict, db: Session
    ) -> None:
        results = [
            client.post(WEBHOOK, json=webhook_body(pending)).json()["result"] for _ in range(10)
        ]
        assert Counter(results) == {"processed": 1, "duplicate": 9}
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"

    def test_duplicate_failed_webhook_does_not_touch_a_later_state(
        self, client: TestClient, auth_headers: dict, pending: dict
    ) -> None:
        client.post(WEBHOOK, json=webhook_body(pending, "evt_fail", "FAILED"))
        replay = client.post(WEBHOOK, json=webhook_body(pending, "evt_fail", "FAILED"))
        assert replay.json()["result"] == "duplicate"
        assert booking_status(client, auth_headers, pending["booking_id"]) == "FAILED"

    def test_conflicting_late_event_is_ignored(
        self, client: TestClient, auth_headers: dict, pending: dict
    ) -> None:
        client.post(WEBHOOK, json=webhook_body(pending, "evt_ok", "SUCCESS"))
        late = client.post(WEBHOOK, json=webhook_body(pending, "evt_late", "FAILED"))
        assert (late.status_code, late.json()["result"]) == (200, "ignored")
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"

    def test_webhook_for_payment_already_settled_synchronously(
        self, client: TestClient, auth_headers: dict, catalogue: SimpleNamespace
    ) -> None:
        booking = book(client, auth_headers, catalogue.alpha.id, catalogue.hba1c.id).json()
        payment = pay(client, auth_headers, booking["id"], "success").json()
        response = client.post(WEBHOOK, json=webhook_body(payment))
        assert response.json()["result"] == "processed"  # provider confirmation: state unchanged
        assert booking_status(client, auth_headers, booking["id"]) == "CONFIRMED"

    @pytest.mark.parametrize("event_id", ["", " ", "has space", "x" * 200, "semi;colon"])
    def test_invalid_event_id(self, client: TestClient, pending: dict, event_id: str) -> None:
        response = client.post(WEBHOOK, json={**webhook_body(pending), "event_id": event_id})
        assert response.status_code == 422

    def test_missing_event_id(self, client: TestClient, pending: dict) -> None:
        body = webhook_body(pending)
        del body["event_id"]
        assert client.post(WEBHOOK, json=body).status_code == 422

    def test_inconsistent_or_unknown_event_type_and_status(
        self, client: TestClient, pending: dict
    ) -> None:
        wrong = {**webhook_body(pending), "event_type": "payment.failed"}  # status says SUCCESS
        assert client.post(WEBHOOK, json=wrong).status_code == 422
        unknown = {**webhook_body(pending), "event_type": "refund.created"}
        assert client.post(WEBHOOK, json=unknown).status_code == 422
        bad_status = {**webhook_body(pending), "status": "MAYBE"}
        assert client.post(WEBHOOK, json=bad_status).status_code == 422

    def test_malformed_json(self, client: TestClient) -> None:
        response = client.post(
            WEBHOOK, content="{oops", headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422

    def test_unknown_payment(self, client: TestClient, pending: dict) -> None:
        body = {**webhook_body(pending), "payment_id": "pay_unknown"}
        response = client.post(WEBHOOK, json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (
            404,
            "PAYMENT_NOT_FOUND",
        )

    def test_invalid_booking(self, client: TestClient, pending: dict) -> None:
        body = {**webhook_body(pending), "booking_id": "00000000-0000-4000-8000-000000000000"}
        response = client.post(WEBHOOK, json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (
            400,
            "WEBHOOK_PAYMENT_BOOKING_MISMATCH",
        )
        assert client.post(WEBHOOK, json={**body, "booking_id": "nope"}).status_code == 422

    @pytest.mark.concurrency
    def test_concurrent_duplicate_webhooks_over_http(
        self, client: TestClient, auth_headers: dict, pending: dict, db: Session
    ) -> None:
        """Ten simultaneous HTTP deliveries of one event: one 'processed', nine 'duplicate'."""
        barrier = threading.Barrier(10)
        results: list[str] = []
        lock = threading.Lock()

        def deliver() -> None:
            from fastapi.testclient import TestClient as Client

            with Client(app) as own_client:  # each thread gets its own client/connection
                barrier.wait()
                result = own_client.post(WEBHOOK, json=webhook_body(pending)).json()["result"]
            with lock:
                results.append(result)

        threads = [threading.Thread(target=deliver) for _ in range(10)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        assert Counter(results) == {"processed": 1, "duplicate": 9}
        assert db.scalar(select(func.count()).select_from(WebhookEvent)) == 1
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"


class TestWebhookRetryOverHttp:
    def test_transient_failure_returns_202_and_enqueues_a_retry(
        self, client: TestClient, auth_headers: dict, pending: dict, monkeypatch
    ) -> None:
        scheduled: list[dict] = []
        calls = {"n": 0}
        original_apply = WebhookService._apply

        def flaky_apply(self, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("UPDATE", {}, Exception("connection reset"))
            return original_apply(self, payload)

        monkeypatch.setattr(WebhookService, "_apply", flaky_apply)

        def service_with_recording_scheduler():
            with SessionLocal() as session:  # real database, fake Celery scheduler
                yield WebhookService(session, retry_scheduler=scheduled.append)

        app.dependency_overrides[get_webhook_service] = service_with_recording_scheduler

        response = client.post(WEBHOOK, json=webhook_body(pending))
        assert (response.status_code, response.json()["result"]) == (202, "queued")
        assert booking_status(client, auth_headers, pending["booking_id"]) == "PENDING"
        assert len(scheduled) == 1

        # The Celery task would now re-run the same payload; the second attempt succeeds.
        retry = client.post(WEBHOOK, json=scheduled[0])
        assert retry.json()["result"] == "processed"
        assert booking_status(client, auth_headers, pending["booking_id"]) == "CONFIRMED"


class TestWebhookSignature:
    @pytest.fixture(autouse=True)
    def _require_signature(self, monkeypatch) -> None:
        monkeypatch.setattr(get_settings(), "webhook_signature_required", True)

    def _post(self, client: TestClient, body: dict, signature: str | None):
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-Webhook-Signature"] = signature
        return client.post(WEBHOOK, content=raw, headers=headers), raw

    def test_valid_signature_accepted(self, client: TestClient, pending: dict) -> None:
        body = webhook_body(pending)
        raw = json.dumps(body).encode()
        signature = sign_webhook_body(raw, get_settings().webhook_secret)
        response, _ = self._post(client, body, signature)
        assert response.status_code == 200

    def test_missing_or_wrong_signature_rejected_and_nothing_changes(
        self, client: TestClient, auth_headers: dict, pending: dict
    ) -> None:
        body = webhook_body(pending)
        for signature in (None, "sha256=deadbeef", sign_webhook_body(b"{}", "other-secret")):
            response, _ = self._post(client, body, signature)
            assert (response.status_code, response.json()["error"]["code"]) == (
                401,
                "INVALID_WEBHOOK_SIGNATURE",
            )
        assert booking_status(client, auth_headers, pending["booking_id"]) == "PENDING"
