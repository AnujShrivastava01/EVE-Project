"""Idempotent processing of payment-provider webhooks.

THE ALGORITHM (all inside ONE database transaction):

  1. claim   INSERT INTO webhook_events (event_id, ...) ON CONFLICT (event_id) DO NOTHING
             -> no row back  => this event_id was already handled: roll back, answer "duplicate".
  2. lock    SELECT booking ... FOR UPDATE, then payment ... FOR UPDATE   (fixed lock order)
  3. apply   payment PENDING -> SUCCESS/FAILED and booking PENDING -> CONFIRMED/FAILED, using the
             explicit state machines (so an event can never move a settled payment again)
  4. finish  mark the event PROCESSED/IGNORED, COMMIT.

Why this is safe under concurrency: the UNIQUE index on event_id makes step 1 a database-level
mutex per event. N simultaneous deliveries => one claims, the others block on the index entry
until it commits (then see "already exists") or rolls back (then one of them claims). If anything
fails after step 1 the whole transaction rolls back, including the claim, so a retry starts clean.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import (
    AppError,
    PaymentNotFoundError,
    ServiceUnavailableError,
    TransientWebhookError,
    WebhookMismatchError,
)
from app.core.logging import bind_log_context
from app.db.models import BookingStatus, PaymentStatus, WebhookEventStatus
from app.repositories.booking_repository import BookingRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.webhook_repository import WebhookRepository
from app.schemas.webhook import WebhookOutcome, WebhookPayload

logger = logging.getLogger(__name__)

RetryScheduler = Callable[[dict[str, Any]], None]


class WebhookService:
    def __init__(self, db: Session, retry_scheduler: RetryScheduler | None = None) -> None:
        self.db = db
        self.events = WebhookRepository(db)
        self.payments = PaymentRepository(db)
        self.bookings = BookingRepository(db)
        self._retry_scheduler = retry_scheduler

    # --- entry point used by the HTTP layer ---------------------------------------------
    def receive(self, payload: WebhookPayload) -> WebhookOutcome:
        """Process now; if the infrastructure hiccups, hand the event to Celery for retries."""
        try:
            return self.process(payload)
        except TransientWebhookError:
            return self._schedule_retry(payload)

    def _schedule_retry(self, payload: WebhookPayload) -> WebhookOutcome:
        if self._retry_scheduler is None:
            raise ServiceUnavailableError()
        try:
            self._retry_scheduler(payload.model_dump(mode="json"))
        except Exception as exc:  # broker down: let the provider redeliver instead
            logger.error("webhook_retry_enqueue_failed", exc_info=exc)
            raise ServiceUnavailableError() from exc
        logger.warning("webhook_queued_for_retry", extra={"webhook_event_id": payload.event_id})
        return WebhookOutcome.QUEUED

    # --- the idempotent core (also called by the Celery retry task) -----------------------
    def process(self, payload: WebhookPayload, retry_count: int = 0) -> WebhookOutcome:
        bind_log_context(
            webhook_event_id=payload.event_id,
            booking_id=str(payload.booking_id),
            payment_id=payload.payment_id,
        )
        try:
            # 1. claim the event_id (database-level idempotency)
            event = self.events.claim(
                payload.event_id, payload.event_type.value, payload.model_dump(mode="json")
            )
            if event is None:
                self.db.rollback()
                logger.info("webhook_duplicate")
                return WebhookOutcome.DUPLICATE

            # 2-3. lock rows and apply the state change
            event.status = self._apply(payload)
            event.retry_count = retry_count
            event.processed_at = datetime.now(UTC)

            # 4. commit claim + state change atomically
            self.db.commit()
        except AppError:
            self.db.rollback()  # permanent problem (unknown payment, mismatch...): don't retry
            logger.warning("webhook_rejected", exc_info=True)
            raise
        except OperationalError as exc:  # deadlock, serialization failure, lost connection
            self.db.rollback()
            logger.warning("webhook_transient_failure", extra={"retry_count": retry_count})
            raise TransientWebhookError(str(exc)) from exc

        outcome = (
            WebhookOutcome.PROCESSED
            if event.status is WebhookEventStatus.PROCESSED
            else WebhookOutcome.IGNORED
        )
        logger.info("webhook_processed", extra={"outcome": outcome.value})
        return outcome

    def _apply(self, payload: WebhookPayload) -> WebhookEventStatus:
        payment = self.payments.get_by_provider_id(payload.payment_id)
        if payment is None:
            raise PaymentNotFoundError()
        if payment.booking_id != payload.booking_id:
            raise WebhookMismatchError()

        # Lock order everywhere in the code base: booking first, then payment (no deadlocks).
        booking = self.bookings.get_for_update(payment.booking_id)
        payment = self.payments.get_for_update(payment.id)
        target = PaymentStatus(payload.status)

        if payment.status is target:
            return WebhookEventStatus.PROCESSED  # already in the requested state: nothing to do
        if not payment.can_transition_to(target):
            # e.g. a late "success" for a payment we already failed (or vice versa).
            logger.warning(
                "webhook_conflicts_with_payment_state",
                extra={"payment_status": payment.status.value, "event_status": payload.status},
            )
            return WebhookEventStatus.IGNORED

        if target is PaymentStatus.SUCCESS:
            payment.transition_to(PaymentStatus.SUCCESS)
            booking.transition_to(BookingStatus.CONFIRMED)
        else:
            payment.transition_to(PaymentStatus.FAILED, "Reported as failed by payment provider")
            booking.transition_to(BookingStatus.FAILED)
        return WebhookEventStatus.PROCESSED

    # --- used by the retry task when it gives up -------------------------------------------
    def record_failed_event(self, payload: WebhookPayload, retry_count: int, error: str) -> None:
        self.db.rollback()
        self.events.record_failed(
            payload.event_id,
            payload.event_type.value,
            payload.model_dump(mode="json"),
            retry_count,
            error,
        )
        self.db.commit()
