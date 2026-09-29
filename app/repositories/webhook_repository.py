from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import WebhookEvent, WebhookEventStatus


class WebhookRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def claim(self, event_id: str, event_type: str, payload: dict[str, Any]) -> WebhookEvent | None:
        """Try to be the one and only processor of `event_id`.

        INSERT ... ON CONFLICT (event_id) DO NOTHING is decided by the UNIQUE index, not by
        Python. If another transaction is inserting the same event_id right now, PostgreSQL makes
        this statement WAIT for that transaction to finish: if it commits we get no row back
        (=> duplicate), if it rolls back we insert and proceed. Either way exactly one
        transaction ever owns the event.

        Returns the new row, or None if the event was already claimed.
        """
        statement = (
            pg_insert(WebhookEvent)
            .values(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                status=WebhookEventStatus.PROCESSING,
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent.id)
        )
        new_id = self.db.execute(statement).scalar_one_or_none()
        return self.db.get(WebhookEvent, new_id) if new_id else None

    def record_failed(
        self,
        event_id: str,
        event_type: str,
        payload: dict[str, Any],
        retry_count: int,
        error_message: str,
    ) -> bool:
        """Persist a give-up record (retries exhausted / permanent error) for later inspection."""
        statement = (
            pg_insert(WebhookEvent)
            .values(
                event_id=event_id,
                event_type=event_type,
                payload=payload,
                status=WebhookEventStatus.FAILED,
                retry_count=retry_count,
                error_message=error_message[:1000],
            )
            .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
            .returning(WebhookEvent.id)
        )
        return self.db.execute(statement).scalar_one_or_none() is not None

    def get_by_event_id(self, event_id: str) -> WebhookEvent | None:
        return self.db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
