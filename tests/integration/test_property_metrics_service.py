"""
Integration tests for property metric computation service.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.event import Events, EventTypes
from app.models.tenant import Tenants
from app.services.property_metrics import compute_property_metrics


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_event_type(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    tracked_properties: dict | None = None,  # type: ignore[type-arg]
) -> EventTypes:
    meta = {"tracked_properties": tracked_properties} if tracked_properties else {}
    et = EventTypes(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        first_seen=datetime.datetime.now(datetime.UTC),
        last_seen=datetime.datetime.now(datetime.UTC),
        total_events=0,
        type_metadata=meta if meta else None,
    )
    db.add(et)
    db.flush()
    return et


def _insert_event(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    properties: dict,  # type: ignore[type-arg]
    minutes_ago: float = 5.0,
) -> None:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    db.add(Events(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        event_name=event_name,
        timestamp=ts,
        ingested_at=datetime.datetime.now(datetime.UTC),
        properties=properties,
    ))
    db.flush()


@pytest.mark.integration
def test_no_event_types_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_event_type_with_no_tracked_properties_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(db, tenant_id, "page_view", tracked_properties=None)
    _insert_event(db, tenant_id, "page_view", {"duration_ms": "1200"})

    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_computes_avg_and_p95_for_tracked_property(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg", "p95"]})
    for amount in [10.0, 20.0, 30.0, 40.0, 50.0]:
        _insert_event(db, tenant_id, "checkout", {"amount": str(amount)})

    metrics = compute_property_metrics(db, tenant_id)
    names = {m.metric_name for m in metrics}
    assert "property.checkout.amount.avg" in names
    assert "property.checkout.amount.p95" in names

    avg_metric = next(m for m in metrics if m.metric_name == "property.checkout.amount.avg")
    assert avg_metric.value == pytest.approx(30.0)
    assert avg_metric.tags == {"event_name": "checkout", "property": "amount", "aggregation": "avg"}


@pytest.mark.integration
def test_computes_only_requested_aggregations(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg"]})
    for amount in [10.0, 20.0, 30.0]:
        _insert_event(db, tenant_id, "checkout", {"amount": str(amount)})

    metrics = compute_property_metrics(db, tenant_id)
    names = [m.metric_name for m in metrics]
    assert "property.checkout.amount.avg" in names
    assert "property.checkout.amount.p95" not in names


@pytest.mark.integration
def test_skips_non_numeric_property_values(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(db, tenant_id, "signup", tracked_properties={"plan": ["avg", "p95"]})
    for _ in range(10):
        _insert_event(db, tenant_id, "signup", {"plan": "pro"})

    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_min_presence_rate_threshold(db: Session, tenant_id: uuid.UUID) -> None:
    """Fewer than MIN_PRESENCE_RATE fraction of events having the property → no metric written."""
    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg"]})

    # Only 1 out of 10 events has the property (10% < 30% threshold)
    _insert_event(db, tenant_id, "checkout", {"amount": "99.0"})
    for _ in range(9):
        _insert_event(db, tenant_id, "checkout", {})

    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_above_min_presence_rate_writes_metric(db: Session, tenant_id: uuid.UUID) -> None:
    """When presence rate >= MIN_PRESENCE_RATE, the metric is written."""
    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg"]})

    # 4 out of 10 events have the property (40% >= 30%)
    for _ in range(4):
        _insert_event(db, tenant_id, "checkout", {"amount": "50.0"})
    for _ in range(6):
        _insert_event(db, tenant_id, "checkout", {})

    metrics = compute_property_metrics(db, tenant_id)
    assert len(metrics) == 1
    assert metrics[0].metric_name == "property.checkout.amount.avg"


@pytest.mark.integration
def test_multiple_tracked_properties(db: Session, tenant_id: uuid.UUID) -> None:
    _make_event_type(
        db, tenant_id, "checkout",
        tracked_properties={"amount": ["avg", "p95"], "items_count": ["avg"]},
    )
    for i in range(5):
        _insert_event(db, tenant_id, "checkout", {"amount": str(float(i + 1) * 10), "items_count": str(i + 1)})

    metrics = compute_property_metrics(db, tenant_id)
    names = {m.metric_name for m in metrics}
    assert names == {
        "property.checkout.amount.avg",
        "property.checkout.amount.p95",
        "property.checkout.items_count.avg",
    }


@pytest.mark.integration
def test_events_outside_window_excluded(db: Session, tenant_id: uuid.UUID) -> None:
    """Events older than metric_window_minutes should not be included."""
    from app.config import settings
    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg"]})

    # Insert events well outside the window
    old_minutes = settings.metric_window_minutes + 10
    for _ in range(5):
        _insert_event(db, tenant_id, "checkout", {"amount": "100.0"}, minutes_ago=old_minutes)

    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []


@pytest.mark.integration
def test_tenant_isolation(db: Session, tenant_id: uuid.UUID) -> None:
    """Events for a different tenant must not affect property metrics."""
    other_tenant_id = uuid.uuid4()
    db.add(Tenants(id=other_tenant_id, name="other-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()

    _make_event_type(db, tenant_id, "checkout", tracked_properties={"amount": ["avg"]})
    # Insert events only for the OTHER tenant
    for _ in range(5):
        _insert_event(db, other_tenant_id, "checkout", {"amount": "100.0"})

    metrics = compute_property_metrics(db, tenant_id)
    assert metrics == []
