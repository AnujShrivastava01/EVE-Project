"""Structured JSON logging with a per-request context (request id, user id, ...).

The context lives in a ContextVar holding a *mutable dict*. Sync FastAPI endpoints run in a
thread pool with a *copy* of the context, so re-assigning the variable there would be lost; but
mutating the shared dict is visible everywhere (including the access-log middleware).
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_log_context: ContextVar[dict[str, Any] | None] = ContextVar("log_context", default=None)

# Attributes every LogRecord has; anything else was passed through `extra=` and is emitted.
_STANDARD_RECORD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def init_log_context(**values: Any) -> dict[str, Any]:
    context: dict[str, Any] = dict(values)
    _log_context.set(context)
    return context


def bind_log_context(**values: Any) -> None:
    """Attach fields (user_id, booking_id, ...) to every subsequent log line of this request."""
    context = _log_context.get()
    if context is not None:
        context.update(values)


def get_log_context() -> dict[str, Any]:
    return _log_context.get() or {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(get_log_context())
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # uvicorn's own access log is replaced by our request middleware (one JSON line per request).
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for noisy in ("uvicorn", "uvicorn.error"):
        logging.getLogger(noisy).handlers = []
        logging.getLogger(noisy).propagate = True
