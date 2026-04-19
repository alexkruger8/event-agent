"""Integration tests for the error ingestion and management API."""
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


def _ingest(client: TestClient, tenant_id: uuid.UUID, **overrides: object) -> dict:  # type: ignore[type-arg]
    payload = {
        "error_type": "ValueError",
        "message": "something went wrong",
        "service": "api",
        "severity": "error",
        **overrides,
    }
    r = client.post(f"/errors/{tenant_id}", json=payload)
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


@pytest.mark.integration
def test_ingest_error_returns_201(client: TestClient, tenant_id: uuid.UUID) -> None:
    body = _ingest(client, tenant_id)
    assert body["error_type"] == "ValueError"
    assert body["message"] == "something went wrong"
    assert body["service"] == "api"
    assert body["occurrence_count"] == 1
    assert body["resolved_at"] is None
    assert "id" in body
    assert "fingerprint" in body


@pytest.mark.integration
def test_ingest_error_deduplicates_by_fingerprint(client: TestClient, tenant_id: uuid.UUID) -> None:
    first = _ingest(client, tenant_id)
    second = _ingest(client, tenant_id)
    assert first["id"] == second["id"]
    assert second["occurrence_count"] == 2


@pytest.mark.integration
def test_ingest_error_different_service_creates_new_row(client: TestClient, tenant_id: uuid.UUID) -> None:
    a = _ingest(client, tenant_id, service="api")
    b = _ingest(client, tenant_id, service="worker")
    assert a["id"] != b["id"]


@pytest.mark.integration
def test_ingest_error_fingerprint_is_deterministic(client: TestClient, tenant_id: uuid.UUID) -> None:
    a = _ingest(client, tenant_id)
    b = _ingest(client, tenant_id)
    assert a["fingerprint"] == b["fingerprint"]


@pytest.mark.integration
def test_ingest_error_without_optional_fields(client: TestClient, tenant_id: uuid.UUID) -> None:
    r = client.post(
        f"/errors/{tenant_id}",
        json={"error_type": "RuntimeError", "message": "crash"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["service"] is None
    assert body["severity"] == "error"


@pytest.mark.integration
def test_list_errors_returns_unresolved(client: TestClient, tenant_id: uuid.UUID) -> None:
    _ingest(client, tenant_id, error_type="ErrA")
    _ingest(client, tenant_id, error_type="ErrB")
    r = client.get(f"/errors/{tenant_id}")
    assert r.status_code == 200
    types = {e["error_type"] for e in r.json()}
    assert "ErrA" in types
    assert "ErrB" in types


@pytest.mark.integration
def test_get_error_detail(client: TestClient, tenant_id: uuid.UUID) -> None:
    created = _ingest(client, tenant_id)
    r = client.get(f"/errors/{tenant_id}/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


@pytest.mark.integration
def test_get_error_detail_404_for_wrong_tenant(client: TestClient, db: Session) -> None:
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    for tid in (tid_a, tid_b):
        db.add(Tenants(id=tid, name=f"t-{tid}", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()

    created = _ingest(client, tid_a)
    r = client.get(f"/errors/{tid_b}/{created['id']}")
    assert r.status_code == 404


@pytest.mark.integration
def test_resolve_error(client: TestClient, tenant_id: uuid.UUID) -> None:
    created = _ingest(client, tenant_id)
    r = client.patch(f"/errors/{tenant_id}/{created['id']}/resolve")
    assert r.status_code == 200
    assert r.json()["resolved_at"] is not None


@pytest.mark.integration
def test_resolved_error_excluded_when_filtering_unresolved(client: TestClient, tenant_id: uuid.UUID) -> None:
    created = _ingest(client, tenant_id)
    client.patch(f"/errors/{tenant_id}/{created['id']}/resolve")
    r = client.get(f"/errors/{tenant_id}", params={"resolved": "false"})
    ids = [e["id"] for e in r.json()]
    assert created["id"] not in ids


@pytest.mark.integration
def test_resolved_error_restarts_dedup(client: TestClient, tenant_id: uuid.UUID) -> None:
    first = _ingest(client, tenant_id)
    client.patch(f"/errors/{tenant_id}/{first['id']}/resolve")
    second = _ingest(client, tenant_id)
    assert second["id"] != first["id"]
    assert second["occurrence_count"] == 1


@pytest.mark.integration
def test_batch_ingest_errors(client: TestClient, tenant_id: uuid.UUID) -> None:
    payload = {"errors": [
        {"error_type": "ErrA", "message": "msg a"},
        {"error_type": "ErrB", "message": "msg b"},
    ]}
    r = client.post(f"/errors/{tenant_id}/batch", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["accepted"] == 2
