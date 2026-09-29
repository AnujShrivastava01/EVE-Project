import fakeredis
import redis
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.redis import get_redis
from app.main import app
from tests.factories import PASSWORD

LOGIN = "/api/v1/auth/login"
CREDENTIALS = {"email": "nobody@example.com", "password": PASSWORD}


def test_requests_within_the_limit_are_allowed(client: TestClient, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_window", 3)
    for _ in range(3):
        assert client.post(LOGIN, json=CREDENTIALS).status_code == 401  # allowed (bad creds)


def test_exceeding_the_limit_returns_429_with_retry_after(client: TestClient, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_window", 2)
    for _ in range(2):
        client.post(LOGIN, json=CREDENTIALS)
    blocked = client.post(LOGIN, json=CREDENTIALS)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert 1 <= int(blocked.headers["Retry-After"]) <= settings.rate_limit_window_seconds


def test_limits_are_per_scope_and_the_window_resets(
    client: TestClient, fake_redis: fakeredis.FakeRedis, monkeypatch
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_window", 1)
    client.post(LOGIN, json=CREDENTIALS)
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 429
    # webhook has its own counter: unaffected by the auth limit
    assert client.post("/api/v1/payments/webhook/", json={}).status_code == 422
    # once the window key expires, requests flow again
    for key in fake_redis.keys("eve:ratelimit:*"):
        fake_redis.delete(key)
    assert client.post(LOGIN, json=CREDENTIALS).status_code == 401


def test_payment_and_webhook_endpoints_are_limited_too(client: TestClient, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_webhook_per_window", 1)
    monkeypatch.setattr(settings, "rate_limit_payment_per_window", 1)
    assert client.post("/api/v1/payments/webhook/", json={}).status_code == 422
    assert client.post("/api/v1/payments/webhook/", json={}).status_code == 429
    assert client.post("/api/v1/payments/", json={}).status_code == 401  # allowed through to auth
    assert client.post("/api/v1/payments/", json={}).status_code == 429


def test_limiter_fails_open_when_redis_is_down(client: TestClient, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_auth_per_window", 1)
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2, socket_timeout=0.2)
    app.dependency_overrides[get_redis] = lambda: dead
    for _ in range(3):
        assert client.post(LOGIN, json=CREDENTIALS).status_code == 401
