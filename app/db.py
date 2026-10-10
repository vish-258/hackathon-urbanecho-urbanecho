"""Connection health checks and bounded reconnects; transactions are not replayed."""
import logging
import time
from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, InterfaceError
from sqlalchemy.orm import Session

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def get_engine():
    settings = get_settings()
    return create_engine(
        settings.database_url, pool_pre_ping=True, pool_recycle=300,
        pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow,
        connect_args={"connect_timeout": 5, "application_name": "noise-monitor", "options": "-c timezone=UTC"},
    )


def SessionLocal() -> Session:
    return Session(bind=get_engine(), expire_on_commit=False)


def wait_for_database() -> None:
    settings = get_settings()
    for attempt in range(settings.db_retry_attempts):
        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except (OperationalError, InterfaceError):
            if attempt + 1 == settings.db_retry_attempts:
                raise
            logger.warning("Database unavailable; reconnecting (%s/%s)",
                           attempt + 1, settings.db_retry_attempts)
            time.sleep(settings.db_retry_delay_seconds * min(2 ** attempt, 8))


def get_db():
    wait_for_database()
    with SessionLocal() as session:
        yield session
