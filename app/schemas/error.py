"""Error envelope (for OpenAPI docs) and a helper to declare which errors an endpoint can return."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    code: str = Field(examples=["BOOKING_NOT_FOUND"])
    message: str = Field(examples=["Booking not found"])
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


_DESCRIPTIONS = {
    400: "Malformed business request",
    401: "Missing, invalid or expired credentials",
    403: "Authenticated but not allowed",
    404: "Resource not found",
    409: "Conflict with the current resource state",
    422: "Request validation failed",
    429: "Rate limit exceeded",
    503: "Dependency temporarily unavailable",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """Usage: @router.get(..., responses=error_responses(401, 404))"""
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in status_codes
    }
