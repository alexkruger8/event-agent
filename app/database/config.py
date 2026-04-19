import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()

class DatabaseSettings:
    url: str
    pool_size: int
    max_overflow: int
    pool_timeout: int
    pool_recycle: int
    echo: bool

    def __init__(self) -> None:
        self.url = os.getenv("DATABASE_URL") or ""

        self.pool_size = int(os.getenv("DB_POOL_SIZE", 5))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", 10))
        self.pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", 30))
        self.pool_recycle = int(os.getenv("DB_POOL_RECYCLE", 1800))
        self.echo = os.getenv("SQLALCHEMY_ECHO", "false").lower() == "true"

@lru_cache
def get_db_settings() -> DatabaseSettings:
    return DatabaseSettings()
