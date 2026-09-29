"""Assigns a request id and emits one structured access-log line per request."""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.exceptions import unexpected_error_response
from app.core.logging import get_log_context, init_log_context

logger = logging.getLogger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_INCOMING_ID_LENGTH = 64


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = (
            incoming[:_MAX_INCOMING_ID_LENGTH] if incoming.isprintable() and incoming else None
        )
        init_log_context(request_id=request_id or uuid.uuid4().hex)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Last line of defence: log the stack trace server-side, return a generic 500.
            logger.exception("unhandled_exception")
            response = unexpected_error_response()

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = get_log_context()["request_id"]
        logger.info(
            "request_completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        return response
