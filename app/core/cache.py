"""Tiny JSON cache on top of Redis.

Invalidation strategy: every key embeds a *namespace version* (`eve:cache:catalogue:v<N>:...`).
Bumping the version (one INCR) instantly orphans every cached list/detail page for that
namespace; the orphans expire via TTL. This avoids SCAN/DEL across query-string variants.

Redis is optional: every operation swallows Redis errors, so the API falls back to PostgreSQL.
"""

import logging

import redis
from pydantic import BaseModel

logger = logging.getLogger(__name__)

CATALOGUE_NAMESPACE = "catalogue"


class Cache:
    def __init__(self, client: redis.Redis, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    def _version(self, namespace: str) -> str:
        return self._client.get(f"eve:cache:{namespace}:version") or "0"

    def build_key(self, namespace: str, *parts: object) -> str:
        return f"eve:cache:{namespace}:v{self._version(namespace)}:" + ":".join(map(str, parts))

    def get(self, namespace: str, *parts: object) -> str | None:
        try:
            return self._client.get(self.build_key(namespace, *parts))
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"operation": "get"})
            return None

    def set(self, namespace: str, *parts: object, value: BaseModel) -> None:
        try:
            self._client.set(
                self.build_key(namespace, *parts), value.model_dump_json(), ex=self._ttl
            )
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"operation": "set"})

    def invalidate(self, namespace: str) -> None:
        try:
            self._client.incr(f"eve:cache:{namespace}:version")
        except redis.RedisError:
            # Stale entries are bounded by the TTL.
            logger.warning("cache_unavailable", extra={"operation": "invalidate"})
