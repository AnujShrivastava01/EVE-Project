"""Background jobs. Only work that genuinely benefits from being async lives here:

* process_webhook_event : retry of a webhook that hit a transient failure (backoff, bounded)
* expire_stale_payments : periodic cleanup of PENDING payments that never got a webhook
"""

import logging
from typing import Any

from celery import Task

from app.core.config import get_settings
from app.core.exceptions import AppError, TransientWebhookError
from app.db.database import SessionLocal
from app.schemas.webhook import WebhookPayload
from app.services.payment_service import PaymentService
from app.services.webhook_service import WebhookService
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def backoff_seconds(retry_number: int) -> int:
    """Delay before Celery run `retry_number` (1-based): 1s, 5s, 15s, 30s, 60s by default."""
    schedule = get_settings().webhook_retry_backoff_seconds
    return schedule[min(max(retry_number, 1), len(schedule)) - 1]


def enqueue_webhook_retry(payload: dict[str, Any]) -> None:
    """Passed to WebhookService as its retry scheduler."""
    process_webhook_event.apply_async(args=[payload], countdown=backoff_seconds(1))


@celery_app.task(bind=True, name="webhooks.process_event", acks_late=True)
def process_webhook_event(self: Task, payload: dict[str, Any]) -> str:
    """Re-run webhook processing after a transient failure.

    Idempotent by construction (event_id claim + payment state machine), so it is safe if Celery
    delivers this task twice or the API also gets a provider redelivery meanwhile.
    """
    max_runs = get_settings().webhook_max_retries
    run = self.request.retries + 1  # 1-based number of this retry attempt
    event = WebhookPayload.model_validate(payload)

    with SessionLocal() as db:
        service = WebhookService(db)
        try:
            outcome = service.process(event, retry_count=run)
        except TransientWebhookError as exc:
            if run >= max_runs:
                logger.error(
                    "webhook_retries_exhausted",
                    extra={"webhook_event_id": event.event_id, "retry_count": run},
                )
                service.record_failed_event(event, run, f"Retries exhausted: {exc}")
                return "failed"
            delay = backoff_seconds(run + 1)
            logger.warning(
                "webhook_retry_scheduled",
                extra={
                    "webhook_event_id": event.event_id,
                    "retry_count": run,
                    "next_delay_seconds": delay,
                },
            )
            raise self.retry(exc=exc, countdown=delay, max_retries=max_runs - 1) from exc
        except AppError as exc:
            # Permanent error (unknown payment, mismatch, ...): retrying can't help.
            logger.error(
                "webhook_permanent_failure",
                extra={"webhook_event_id": event.event_id, "error_code": exc.code},
            )
            service.record_failed_event(event, run, f"{exc.code}: {exc.message}")
            return "failed"
    return outcome.value


@celery_app.task(name="payments.expire_stale")
def expire_stale_payments() -> int:
    with SessionLocal() as db:
        expired = PaymentService(db).expire_stale_payments()
    logger.info("stale_payments_cleanup_done", extra={"expired": expired})
    return expired
