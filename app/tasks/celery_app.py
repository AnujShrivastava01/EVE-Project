"""Celery application: broker/result backend = Redis. Also defines the periodic schedule."""

from celery import Celery

from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)

celery_app = Celery(
    "eve",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.payment_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Re-deliver a task if the worker dies mid-run. Safe because processing is idempotent.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Keep our JSON logging on the root logger instead of Celery's own format.
    worker_hijack_root_logger=False,
    broker_connection_retry_on_startup=True,
    # Fail fast when publishing while Redis is down (the API then answers 503).
    broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 1},
    task_publish_retry_policy={
        "max_retries": 1,
        "interval_start": 0,
        "interval_step": 0.2,
        "interval_max": 0.5,
    },
    beat_schedule={
        "expire-stale-payments": {
            "task": "payments.expire_stale",
            "schedule": 300.0,  # every 5 minutes
        },
    },
)
