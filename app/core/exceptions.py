"""Domain errors and the handlers that turn every failure into one consistent JSON shape:

{"error": {"code": "BOOKING_NOT_FOUND", "message": "Booking not found"}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_log_context

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class: services raise these; the handler below renders them."""

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        details: Any = None,
    ) -> None:
        self.message = message or self.message
        self.headers = headers
        self.details = details
        super().__init__(self.message)


# --- 400: malformed business request ---------------------------------------
class InvalidAppointmentError(AppError):
    status_code, code = 400, "INVALID_APPOINTMENT"
    message = "Invalid appointment time"


class SimulationOverrideDisabledError(AppError):
    status_code, code = 400, "SIMULATION_OVERRIDE_DISABLED"
    message = "Payment outcome simulation is disabled in this environment"


class WebhookMismatchError(AppError):
    status_code, code = 400, "WEBHOOK_PAYMENT_BOOKING_MISMATCH"
    message = "Webhook payment_id does not belong to the given booking_id"


# --- 401 / 403 ---------------------------------------------------------------
class NotAuthenticatedError(AppError):
    status_code, code = 401, "NOT_AUTHENTICATED"
    message = "Not authenticated"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("headers", {"WWW-Authenticate": "Bearer"})
        super().__init__(message, **kwargs)


class InvalidCredentialsError(NotAuthenticatedError):
    code = "INVALID_CREDENTIALS"
    message = "Incorrect email or password"


class InvalidTokenError(NotAuthenticatedError):
    code = "INVALID_TOKEN"
    message = "Invalid or expired token"


class InvalidWebhookSignatureError(AppError):
    status_code, code = 401, "INVALID_WEBHOOK_SIGNATURE"
    message = "Invalid webhook signature"


class UserInactiveError(AppError):
    status_code, code = 403, "USER_INACTIVE"
    message = "User account is disabled"


class BookingAccessDeniedError(AppError):
    status_code, code = 403, "BOOKING_ACCESS_DENIED"
    message = "You do not have access to this booking"


# --- 404 ---------------------------------------------------------------------
class CentreNotFoundError(AppError):
    status_code, code = 404, "CENTRE_NOT_FOUND"
    message = "Diagnostic centre not found"


class DiagnosticTestNotFoundError(AppError):
    status_code, code = 404, "TEST_NOT_FOUND"
    message = "Diagnostic test not found"


class CentreTestNotFoundError(AppError):
    status_code, code = 404, "CENTRE_TEST_NOT_FOUND"
    message = "This test is not offered at the given centre"


class BookingNotFoundError(AppError):
    status_code, code = 404, "BOOKING_NOT_FOUND"
    message = "Booking not found"


class PaymentNotFoundError(AppError):
    status_code, code = 404, "PAYMENT_NOT_FOUND"
    message = "Payment not found"


# --- 409: conflict with current state ---------------------------------------
class EmailAlreadyRegisteredError(AppError):
    status_code, code = 409, "EMAIL_ALREADY_REGISTERED"
    message = "A user with this email already exists"


class CentreUnavailableError(AppError):
    status_code, code = 409, "CENTRE_UNAVAILABLE"
    message = "This diagnostic centre is currently not accepting bookings"


class DiagnosticTestUnavailableError(AppError):
    status_code, code = 409, "TEST_UNAVAILABLE"
    message = "This test is currently unavailable at the selected centre"


class DuplicateBookingError(AppError):
    status_code, code = 409, "DUPLICATE_BOOKING"
    message = "You already have an active booking for this test at this time"


class InvalidBookingTransitionError(AppError):
    status_code, code = 409, "INVALID_BOOKING_TRANSITION"
    message = "Booking status transition is not allowed"


class BookingNotPayableError(AppError):
    status_code, code = 409, "BOOKING_NOT_PAYABLE"
    message = "Booking is not payable in its current state"


class PaymentAlreadyExistsError(AppError):
    status_code, code = 409, "PAYMENT_ALREADY_EXISTS"
    message = "A payment already exists for this booking"


class PaymentInProgressError(AppError):
    status_code, code = 409, "PAYMENT_IN_PROGRESS"
    message = "A payment is in progress for this booking"


# --- 429 / 503 ---------------------------------------------------------------
class RateLimitExceededError(AppError):
    status_code, code = 429, "RATE_LIMIT_EXCEEDED"
    message = "Too many requests, please slow down"


class ServiceUnavailableError(AppError):
    status_code, code = 503, "SERVICE_UNAVAILABLE"
    message = "Service temporarily unavailable, please retry"


class TransientWebhookError(Exception):
    """Webhook processing hit a transient infrastructure failure (deadlock, connection loss...).

    Internal signal only: it triggers a Celery retry and is never rendered to the client."""


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    request_id = get_log_context().get("request_id")
    if request_id:
        error["request_id"] = request_id
    return {"error": error}


def _response(
    status_code: int, code: str, message: str, headers: dict[str, str] | None = None, details=None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content=error_body(code, message, details), headers=headers
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    log = logger.warning if exc.status_code < 500 else logger.error
    log("app_error", extra={"error_code": exc.code, "status_code": exc.status_code})
    return _response(exc.status_code, exc.code, exc.message, exc.headers, exc.details)


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Deliberately drop `input`: it may contain a password.
    details = [
        {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
        for err in exc.errors()
    ]
    return _response(422, "VALIDATION_ERROR", "Request validation failed", details=details)


_HTTP_ERROR_CODES = {
    401: "NOT_AUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
}


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
    return _response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    # Services translate the constraint violations they expect; this is the safety net.
    logger.error("integrity_error", exc_info=exc)
    return _response(409, "CONFLICT", "The request conflicts with existing data")


async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.error("database_error", exc_info=exc)
    return _response(500, "DATABASE_ERROR", "A database error occurred")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, database_error_handler)


def unexpected_error_response() -> JSONResponse:
    """Used by the request middleware for anything unhandled. Never leaks internals."""
    return _response(500, "INTERNAL_ERROR", "Internal server error")
