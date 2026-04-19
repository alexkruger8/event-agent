"""
Integration tests for the metric computation service.
Requires a running database (docker compose up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.event import Events
from app.models.tenant import Tenants
from app.services.metrics import compute_metrics


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _insert_event(db: Session, tenant_id: uuid.UUID, event_name: str, minutes_ago: float = 0) -> None:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    db.add(Events(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        timestamp=ts,
        ingested_at=ts,
        properties={},
    ))


@pytest.mark.integration
def test_counts_events_within_window(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_event(db, tenant_id, "page_view", minutes_ago=0.1)
    _insert_event(db, tenant_id, "page_view", minutes_ago=0.2)
    _insert_event(db, tenant_id, "signup", minutes_ago=0.1)
    db.flush()

    metrics = compute_metrics(db, tenant_id)

    by_name = {m.metric_name: m.value for m in metrics}
    assert by_name["event_count.page_view"] == 2.0
    assert by_name["event_count.signup"] == 1.0


@pytest.mark.integration
def test_excludes_events_outside_window(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_event(db, tenant_id, "page_view", minutes_ago=0.1)
    _insert_event(db, tenant_id, "page_view", minutes_ago=999)
    db.flush()

    metrics = compute_metrics(db, tenant_id)

    by_name = {m.metric_name: m.value for m in metrics}
    assert by_name["event_count.page_view"] == 1.0


@pytest.mark.integration
def test_no_events_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    metrics = compute_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_persists_metric_rows(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_event(db, tenant_id, "click", minutes_ago=0.1)
    db.flush()

    metrics = compute_metrics(db, tenant_id)
    assert len(metrics) == 1
    assert metrics[0].id is not None
    assert metrics[0].tenant_id == tenant_id
    assert metrics[0].tags == {"event_name": "click"}
