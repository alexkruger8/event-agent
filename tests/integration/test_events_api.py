"""
Integration tests for the event ingestion endpoint.
Requires a running database (docker compose up -d).
"""
import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.tenant import Tenants


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


@pytest.mark.integration
def test_ingest_event_returns_201(client: TestClient, tenant_id: uuid.UUID) -> None:
    response = client.post(
        f"/events/{tenant_id}",
        json={"event_name": "page_view", "user_id": "user-123", "properties": {"path": "/home"}},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event_name"] == "page_view"
    assert body["tenant_id"] == str(tenant_id)
    assert body["user_id"] == "user-123"
    assert body["properties"] == {"path": "/home"}
    assert "id" in body
    assert "ingested_at" in body


@pytest.mark.integration
def test_ingest_event_without_optional_fields(client: TestClient, tenant_id: uuid.UUID) -> None:
    response = client.post(
        f"/events/{tenant_id}",
        json={"event_name": "signup"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["event_name"] == "signup"
    assert body["user_id"] is None
    assert body["properties"] == {}


@pytest.mark.integration
def test_second_ingest_updates_event_type_catalog(client: TestClient, tenant_id: uuid.UUID) -> None:
    for _ in range(2):
        response = client.post(
            f"/events/{tenant_id}",
            json={"event_name": "button_click"},
        )
        assert response.status_code == 201
