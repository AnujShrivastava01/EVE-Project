"""Application factory: wires middleware, error handlers, routers and health checks."""

import logging
from typing import Any

import redis
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import setup_logging
from app.core.redis import get_redis
from app.db.database import engine
from app.middleware.request_id import REQUEST_ID_HEADER, RequestContextMiddleware

logger = logging.getLogger(__name__)

DESCRIPTION = """
Diagnostic test booking and **simulated** payment backend for EVE Healthcare.

* Sign up / log in to get a JWT, then click **Authorize** (use the `/auth/token` form flow).
* Browse centres and tests, create a booking (the server computes the price), pay for it, and let
  the (simulated) payment provider confirm via the idempotent webhook.
* Every error uses one shape: `{"error": {"code": "...", "message": "..."}}`.
"""

TAGS_METADATA = [
    {"name": "auth", "description": "Signup, login and the current user."},
    {"name": "centres", "description": "Diagnostic centres and the tests they offer (cached)."},
    {"name": "tests", "description": "The diagnostic test catalogue."},
    {"name": "bookings", "description": "Book, list and cancel appointments."},
    {"name": "payments", "description": "Simulated payments and the provider webhook."},
    {"name": "health", "description": "Liveness / readiness probes."},
]


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS_METADATA,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    # Added last => outermost: request id + access log wrap everything, including CORS.
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    @app.get("/health", tags=["health"], summary="Liveness probe")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], summary="Readiness probe (PostgreSQL + Redis)")
    def ready(response: Response) -> dict[str, Any]:
        # PostgreSQL is required; Redis is optional (cache + rate limiting degrade gracefully).
        checks: dict[str, str] = {}
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            checks["postgres"] = "ok"
        except Exception:
            logger.exception("readiness_postgres_failed")
            checks["postgres"] = "down"
        try:
            get_redis().ping()
            checks["redis"] = "ok"
        except redis.RedisError:
            checks["redis"] = "down"
        status = "ok" if checks["postgres"] == "ok" else "unavailable"
        if status == "unavailable":
            response.status_code = 503
        if status == "ok" and checks["redis"] == "down":
            status = "degraded"
        return {"status": status, "checks": checks}

    return app


app = create_app()
