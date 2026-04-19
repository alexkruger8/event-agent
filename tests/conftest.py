"""
Shared test fixtures.

All integration tests use the `db` fixture which wraps each test in an outer
transaction that is always rolled back at teardown. App code can call
db.commit() freely — it only releases a savepoint, never the outer transaction.
This guarantees a clean slate between tests with no manual DELETE cleanup.
"""
# ruff: noqa: E402,I001
from collections.abc import Generator
import os
from unittest.mock import patch

os.environ.setdefault(
    "DATABASE_URL",
    os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://user:pass@127.0.0.1:5434/events"),
)

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database.engine import get_engine
from app.database.session import get_db
from app.main import app


@pytest.fixture()
def db() -> Generator[Session, None, None]:
    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db: Session) -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_db] = lambda: db
    # Disable auth in tests — middleware checks settings.api_key at request time
    with patch("app.middleware.auth.settings") as mock_settings:
        mock_settings.api_key = None
        yield TestClient(app)
    app.dependency_overrides.clear()
