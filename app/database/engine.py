from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.database.config import get_db_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the database engine, creating it on first use. Requires DATABASE_URL to be set."""
    global _engine
    if _engine is None:
        settings = get_db_settings()
        if not settings.url:
            raise RuntimeError(
                "DATABASE_URL must be set for database access. "
                "Unit tests that do not need a database should mock or avoid importing database-dependent code."
            )
        _engine = create_engine(
            settings.url,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            pool_pre_ping=True,
            echo=settings.echo,
            future=True,
        )
    return _engine
