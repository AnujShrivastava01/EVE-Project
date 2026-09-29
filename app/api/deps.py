"""Shared FastAPI dependencies: DB session, current user, cache."""

from typing import Annotated

import redis
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.cache import Cache
from app.core.config import get_settings
from app.core.logging import bind_log_context
from app.core.redis import get_redis
from app.db.database import get_db
from app.db.models import User
from app.services.auth_service import AuthService

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
