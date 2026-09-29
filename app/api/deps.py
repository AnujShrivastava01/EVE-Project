"""Shared FastAPI dependencies: DB session, current user, cache."""

from typing import Annotated

import redis
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.cache import Cache
from app.core.config import get_settings
from app.core.exceptions import InvalidWebhookSignatureError
from app.core.logging import bind_log_context
from app.core.redis import get_redis
from app.core.security import verify_webhook_signature_header
from app.db.database import get_db
from app.db.models import User
from app.services.auth_service import AuthService
from app.services.webhook_service import WebhookService

# tokenUrl points at the OAuth2 *form* login so Swagger's "Authorize" button works.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")

DbSession = Annotated[Session, Depends(get_db)]


def get_cache(client: Annotated[redis.Redis, Depends(get_redis)]) -> Cache:
    return Cache(client, ttl_seconds=get_settings().cache_ttl_seconds)


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
) -> User:
    user = AuthService(db).get_user_from_token(token)
    bind_log_context(user_id=str(user.id))
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


CacheDep = Annotated[Cache, Depends(get_cache)]


def get_webhook_service(db: DbSession) -> WebhookService:
    # Imported here so the API process only loads Celery machinery when a webhook arrives.
    from app.tasks.payment_tasks import enqueue_webhook_retry

    return WebhookService(db, retry_scheduler=enqueue_webhook_retry)


WebhookServiceDep = Annotated[WebhookService, Depends(get_webhook_service)]


async def verify_webhook_signature(request: Request) -> None:
    """Optional HMAC check (WEBHOOK_SIGNATURE_REQUIRED). Runs on the raw body bytes."""
    settings = get_settings()
    if not settings.webhook_signature_required:
        return
    body = await request.body()
    header = request.headers.get("X-Webhook-Signature", "")
    if not verify_webhook_signature_header(body, header, settings.webhook_secret):
        raise InvalidWebhookSignatureError()
