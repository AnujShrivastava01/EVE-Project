"""Shared Redis client. Short timeouts so a Redis outage degrades fast instead of hanging."""

from functools import lru_cache

import redis

from app.core.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.redis_socket_timeout_seconds,
        socket_timeout=settings.redis_socket_timeout_seconds,
    )
