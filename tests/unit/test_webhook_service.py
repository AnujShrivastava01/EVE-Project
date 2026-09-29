import threading
import uuid
from collections import Counter
from types import SimpleNamespace

import celery.exceptions
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import (
    PaymentNotFoundError,
    ServiceUnavailableError,
    TransientWebhookError,
    WebhookMismatchError,
)
from app.db.database import SessionLocal
from app.db.models import (
    Booking,
    BookingStatus,
    Payment,
    PaymentStatus,
    WebhookEvent,
    WebhookEventStatus,
)
from app.schemas.payment import SimulatedOutcome
from app.schemas.webhook import WebhookOutcome, WebhookPayload
from app.services.payment_service import PaymentService
from app.services.webhook_service import WebhookService
from app.tasks import payment_tasks
from app.tasks.payment_tasks import backoff_seconds, process_webhook_event
from tests.factories import create_booking_via_service, create_user


@pytest.fixture
def pending_payment(db: Session, catalogue: SimpleNamespace) -> Payment:
    booking = create_booking_via_service(db, create_user(db), catalogue)
    return PaymentService(db).pay(booking.user, booking.id, SimulatedOutcome.PENDING)


def event(payment: Payment, event_id: str = "evt_1", status: str = "SUCCESS") -> WebhookPayload:
    return WebhookPayload(
        event_id=event_id,
        event_type="payment.success" if status == "SUCCESS" else "payment.failed",
        payment_id=payment.provider_payment_id,
        booking_id=payment.booking_id,
        status=status,
    )


def counts(db: Session) -> tuple[int, int]:
    db.expire_all()
    return (
        db.scalar(select(func.count()).select_from(Payment)),
        db.scalar(select(func.count()).select_from(WebhookEvent)),
    )


class TestProcess:
    def test_success_event_confirms_booking(self, db: Session, pending_payment: Payment) -> None:
        outcome = WebhookService(db).process(event(pending_payment))
        assert outcome is WebhookOutcome.PROCESSED
        db.expire_all()
        assert db.get(Payment, pending_payment.id).status is PaymentStatus.SUCCESS
        assert db.get(Booking, pending_payment.booking_id).status is BookingStatus.CONFIRMED
        stored = db.scalar(select(WebhookEvent))
        assert stored.status is WebhookEventStatus.PROCESSED
        assert stored.processed_at is not None
        assert stored.payload["payment_id"] == pending_payment.provider_payment_id

    def test_failed_event_fails_booking(self, db: Session, pending_payment: Payment) -> None:
        WebhookService(db).process(event(pending_payment, status="FAILED"))
        db.expire_all()
        payment = db.get(Payment, pending_payment.id)
        assert payment.status is PaymentStatus.FAILED
        assert payment.failure_reason
        assert db.get(Booking, pending_payment.booking_id).status is BookingStatus.FAILED

    @pytest.mark.parametrize("deliveries", [2, 10])
    def test_duplicate_deliveries_change_nothing(
        self, db: Session, pending_payment: Payment, deliveries: int
    ) -> None:
        results = [WebhookService(db).process(event(pending_payment)) for _ in range(deliveries)]
        assert results == [WebhookOutcome.PROCESSED] + [WebhookOutcome.DUPLICATE] * (deliveries - 1)
        assert counts(db) == (1, 1)  # still exactly one payment and one event row
        assert db.get(Booking, pending_payment.booking_id).status is BookingStatus.CONFIRMED

    def test_new_event_id_for_already_settled_payment_is_a_harmless_noop(
        self, db: Session, pending_payment: Payment
    ) -> None:
        WebhookService(db).process(event(pending_payment, "evt_a"))
        outcome = WebhookService(db).process(event(pending_payment, "evt_b"))
        assert outcome is WebhookOutcome.PROCESSED
        assert counts(db) == (1, 2)
        assert db.get(Booking, pending_payment.booking_id).status is BookingStatus.CONFIRMED

    def test_conflicting_late_event_is_recorded_but_ignored(
        self, db: Session, pending_payment: Payment
    ) -> None:
        WebhookService(db).process(event(pending_payment, "evt_a", "SUCCESS"))
        outcome = WebhookService(db).process(event(pending_payment, "evt_b", "FAILED"))
        assert outcome is WebhookOutcome.IGNORED
        db.expire_all()
        assert db.get(Payment, pending_payment.id).status is PaymentStatus.SUCCESS
        assert db.get(Booking, pending_payment.booking_id).status is BookingStatus.CONFIRMED
        ignored = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_b"))
        assert ignored.status is WebhookEventStatus.IGNORED

    def test_unknown_payment_is_a_permanent_error_and_leaves_no_trace(
        self, db: Session, pending_payment: Payment
    ) -> None:
        bad = event(pending_payment).model_copy(update={"payment_id": "pay_does_not_exist"})
        with pytest.raises(PaymentNotFoundError):
            WebhookService(db).process(bad)
        assert counts(db) == (1, 0)  # the event claim was rolled back too

    def test_payment_booking_mismatch_rejected(self, db: Session, pending_payment: Payment) -> None:
        bad = event(pending_payment).model_copy(update={"booking_id": uuid.uuid4()})
        with pytest.raises(WebhookMismatchError):
            WebhookService(db).process(bad)
        db.expire_all()
        assert db.get(Payment, pending_payment.id).status is PaymentStatus.PENDING

    def test_rejected_event_can_be_redelivered_after_the_problem_is_fixed(
        self, db: Session, pending_payment: Payment
    ) -> None:
        good = event(pending_payment)
        bad = good.model_copy(update={"payment_id": "pay_typo"})
        with pytest.raises(PaymentNotFoundError):
            WebhookService(db).process(bad)
        assert WebhookService(db).process(good) is WebhookOutcome.PROCESSED


@pytest.mark.concurrency
@pytest.mark.parametrize("status", ["SUCCESS", "FAILED"])
def test_concurrent_duplicate_webhooks_apply_exactly_once(
    db: Session, pending_payment: Payment, status: str
) -> None:
    """The database (UNIQUE event_id + row locks) serialises 10 simultaneous deliveries."""
    payload = event(pending_payment, status=status)
    barrier = threading.Barrier(10)
    outcomes: list[WebhookOutcome] = []
    lock = threading.Lock()

    def deliver() -> None:
        with SessionLocal() as session:
            barrier.wait()
            outcome = WebhookService(session).process(payload)
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=deliver) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert Counter(outcomes) == {WebhookOutcome.PROCESSED: 1, WebhookOutcome.DUPLICATE: 9}
    assert counts(db) == (1, 1)
    expected_booking = BookingStatus.CONFIRMED if status == "SUCCESS" else BookingStatus.FAILED
    assert db.get(Booking, pending_payment.booking_id).status is expected_booking


@pytest.mark.concurrency
def test_concurrent_different_events_for_one_payment_settle_it_once(
    db: Session, pending_payment: Payment
) -> None:
    """Two *different* event ids racing (success vs failed): the payment settles exactly once."""
    payloads = [
        event(pending_payment, "evt_ok", "SUCCESS"),
        event(pending_payment, "evt_bad", "FAILED"),
    ]
    barrier = threading.Barrier(2)

    def deliver(payload: WebhookPayload) -> None:
        with SessionLocal() as session:
            barrier.wait()
            WebhookService(session).process(payload)

    threads = [threading.Thread(target=deliver, args=(p,)) for p in payloads]
    [t.start() for t in threads]
    [t.join() for t in threads]

    db.expire_all()
    payment = db.get(Payment, pending_payment.id)
    booking = db.get(Booking, pending_payment.booking_id)
    assert (payment.status, booking.status) in {
        (PaymentStatus.SUCCESS, BookingStatus.CONFIRMED),
        (PaymentStatus.FAILED, BookingStatus.FAILED),
    }
    assert counts(db) == (1, 2)


class TestRetryBehaviour:
    @staticmethod
    def _transient_failure(*_args, **_kwargs):
        raise OperationalError("UPDATE payments ...", {}, Exception("deadlock detected"))

    def test_db_hiccup_becomes_transient_error_and_rolls_everything_back(
        self, db: Session, pending_payment: Payment, monkeypatch
    ) -> None:
        payload = event(pending_payment)
        monkeypatch.setattr(WebhookService, "_apply", self._transient_failure)
        with pytest.raises(TransientWebhookError):
            WebhookService(db).process(payload)
        assert counts(db) == (1, 0)  # no claim left behind: a retry can start clean
        db.expire_all()
        assert db.get(Payment, pending_payment.id).status is PaymentStatus.PENDING

        monkeypatch.undo()
        assert WebhookService(db).process(payload) is WebhookOutcome.PROCESSED

    def test_receive_schedules_a_retry_on_transient_failure(
        self, db: Session, pending_payment: Payment, monkeypatch
    ) -> None:
        scheduled: list[dict] = []
        monkeypatch.setattr(WebhookService, "_apply", self._transient_failure)
        service = WebhookService(db, retry_scheduler=scheduled.append)
        assert service.receive(event(pending_payment)) is WebhookOutcome.QUEUED
        assert len(scheduled) == 1
        assert scheduled[0]["event_id"] == "evt_1"

    def test_receive_answers_503_when_it_cannot_schedule_a_retry(
        self, db: Session, pending_payment: Payment, monkeypatch
    ) -> None:
        monkeypatch.setattr(WebhookService, "_apply", self._transient_failure)

        def broker_down(_payload: dict) -> None:
            raise ConnectionError("broker unavailable")

        for scheduler in (None, broker_down):
            service = WebhookService(db, retry_scheduler=scheduler)
            with pytest.raises(ServiceUnavailableError):
                service.receive(event(pending_payment))

    def test_permanent_errors_are_not_retried(self, db: Session, pending_payment: Payment) -> None:
        scheduled: list[dict] = []
        bad = event(pending_payment).model_copy(update={"payment_id": "pay_nope"})
        with pytest.raises(PaymentNotFoundError):
            WebhookService(db, retry_scheduler=scheduled.append).receive(bad)
        assert scheduled == []

    def test_backoff_schedule_is_bounded_and_exponential(self) -> None:
        assert [backoff_seconds(n) for n in range(1, 6)] == [1, 5, 15, 30, 60]
        assert backoff_seconds(99) == 60  # clamped to the last value
        assert backoff_seconds(0) == 1


class TestCeleryTask:
    """Drives the task function directly, stubbing Celery's `retry` so no broker is needed."""

    @pytest.fixture(autouse=True)
    def _retry_stub(self, monkeypatch):
        self.retries: list[dict] = []

        def fake_retry(*, exc, countdown, max_retries):
            self.retries.append({"countdown": countdown, "max_retries": max_retries})
            raise celery.exceptions.Retry(exc=exc)

        monkeypatch.setattr(process_webhook_event, "retry", fake_retry)

    def _run(self, payload: WebhookPayload, retries: int):
        process_webhook_event.push_request(retries=retries, called_directly=False)
        try:
            return process_webhook_event.run(payload.model_dump(mode="json"))
        finally:
            process_webhook_event.pop_request()

    def test_successful_retry_applies_the_event_once(
        self, db: Session, pending_payment: Payment
    ) -> None:
        payload = event(pending_payment)
        assert self._run(payload, retries=0) == "processed"
        assert self._run(payload, retries=1) == "duplicate"  # e.g. Celery redelivery
        assert counts(db) == (1, 1)

    def test_transient_failures_back_off_then_give_up_and_record_failure(
        self, db: Session, pending_payment: Payment, monkeypatch
    ) -> None:
        def always_transient(self, payload, retry_count=0):
            raise TransientWebhookError("db down")

        monkeypatch.setattr(WebhookService, "process", always_transient)
        payload = event(pending_payment)

        for retries in range(4):  # runs 1..4 ask Celery to retry with growing delays
            with pytest.raises(celery.exceptions.Retry):
                self._run(payload, retries=retries)
        assert [r["countdown"] for r in self.retries] == [5, 15, 30, 60]
        assert all(r["max_retries"] == get_settings().webhook_max_retries - 1 for r in self.retries)

        assert self._run(payload, retries=4) == "failed"  # 5th run: retries exhausted
        db.expire_all()
        recorded = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == "evt_1"))
        assert recorded.status is WebhookEventStatus.FAILED
        assert recorded.retry_count == 5
        assert "Retries exhausted" in recorded.error_message
        assert db.get(Payment, pending_payment.id).status is PaymentStatus.PENDING

    def test_permanent_error_is_recorded_without_retrying(
        self, db: Session, pending_payment: Payment
    ) -> None:
        bad = event(pending_payment).model_copy(update={"payment_id": "pay_nope"})
        assert self._run(bad, retries=0) == "failed"
        assert self.retries == []
        recorded = db.scalar(select(WebhookEvent))
        assert recorded.status is WebhookEventStatus.FAILED
        assert "PAYMENT_NOT_FOUND" in recorded.error_message

    def test_enqueue_uses_the_first_backoff_delay(self, monkeypatch) -> None:
        calls = []
        monkeypatch.setattr(process_webhook_event, "apply_async", lambda **kw: calls.append(kw))
        payment_tasks.enqueue_webhook_retry({"event_id": "evt_1"})
        assert calls == [{"args": [{"event_id": "evt_1"}], "countdown": 1}]

    def test_cleanup_task_reports_how_many_payments_it_expired(self, db: Session) -> None:
        assert payment_tasks.expire_stale_payments() == 0
