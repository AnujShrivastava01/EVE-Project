"""Engine / session factory and the per-request session dependency."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

engine = create_engine(
    get_settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=20
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """One session per request. Services call commit(); an unhandled error rolls back on close."""
    with SessionLocal() as session:
        yield session
