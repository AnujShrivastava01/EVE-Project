"""Fixed-window rate limiting backed by Redis, applied as a per-route dependency.

Keyed by client IP + scope (e.g. "login"). Fails OPEN: if Redis is down, requests are allowed
(availability over strictness for a lightweight limiter). Behind a reverse proxy, run uvicorn with
--proxy-headers so request.client reflects the real client.
"""

import logging

import redis
from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.exceptions import RateLimitExceededError
from app.core.redis import get_redis

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, scope: str, limit_setting: str) -> None:
        self.scope = scope
        self.limit_setting = limit_setting  # name of the Settings attribute holding the limit

    def __call__(self, request: Request, client: redis.Redis = Depends(get_redis)) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit = getattr(settings, self.limit_setting)
        window = settings.rate_limit_window_seconds
        identity = request.client.host if request.client else "unknown"
        key = f"eve:ratelimit:{self.scope}:{identity}"
        try:
            client.set(key, 0, ex=window, nx=True)  # create the window if it doesn't exist
            count = client.incr(key)
            retry_after = max(client.ttl(key), 1) if count > limit else 0
        except redis.RedisError:
            logger.warning("rate_limit_unavailable", extra={"scope": self.scope})
            return
        if count > limit:
            logger.warning("rate_limit_exceeded", extra={"scope": self.scope, "limit": limit})
            raise RateLimitExceededError(headers={"Retry-After": str(retry_after)})


limit_auth = RateLimiter("auth", "rate_limit_auth_per_window")
limit_payment = RateLimiter("payment", "rate_limit_payment_per_window")
limit_webhook = RateLimiter("webhook", "rate_limit_webhook_per_window")
