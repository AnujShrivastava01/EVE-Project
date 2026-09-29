import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, bind_log_context, init_log_context
from tests.factories import PASSWORD, book, pay, signup_and_login, webhook_body


def test_formatter_emits_json_with_context_and_extras() -> None:
    init_log_context(request_id="req-1")
    bind_log_context(user_id="u-1")
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello", None, None)
    record.booking_id = "b-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-1"
    assert payload["user_id"] == "u-1"
    assert payload["booking_id"] == "b-1"
    assert "timestamp" in payload


def test_exceptions_are_serialised_into_the_log_line() -> None:
    try:
        raise ValueError("kaboom")
    except ValueError:
        import sys

        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "failed", None, sys.exc_info())
    assert "kaboom" in json.loads(JsonFormatter().format(record))["exception"]


class _CaptureHandler(logging.Handler):
    """Formats at emit time (like the real handler) so per-request context is included."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


@pytest.fixture
def captured() -> _CaptureHandler:
    handler = _CaptureHandler()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    yield handler
    root.removeHandler(handler)
    root.setLevel(previous_level)


def test_access_log_has_request_metadata_and_secrets_never_appear(
    client: TestClient, captured: _CaptureHandler, catalogue
) -> None:
    headers = signup_and_login(client, "log@example.com")
    booking = book(client, headers, catalogue.alpha.id, catalogue.cbc.id).json()
    payment = pay(client, headers, booking["id"], "pending").json()
    client.post("/api/v1/payments/webhook/", json=webhook_body(payment, "evt_log"))

    lines = captured.lines
    raw = json.dumps(lines)
    token = headers["Authorization"].split()[1]
    assert PASSWORD not in raw
    assert token not in raw
    assert "password_hash" not in raw

    access = [line for line in lines if line["message"] == "request_completed"]
    assert access, "one access-log line per request expected"
    for line in access:
        assert {"request_id", "method", "path", "status_code", "duration_ms"} <= line.keys()

    booking_call = next(line for line in access if line["path"] == "/api/v1/bookings")
    assert booking_call["user_id"] and booking_call["booking_id"] == booking["id"]

    webhook_lines = [line for line in lines if line.get("webhook_event_id") == "evt_log"]
    assert webhook_lines
    assert all(line["payment_id"] == payment["provider_payment_id"] for line in webhook_lines)
