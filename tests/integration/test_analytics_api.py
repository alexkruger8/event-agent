"""
Integration tests for the analytics read API.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.models.trend import Trends


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_anomaly(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str = "event_count.page_view",
    severity: str = "high",
    resolved: bool = False,
    acknowledged: bool = False,
) -> Anomalies:
    now = datetime.datetime.now(datetime.UTC)
    a = Anomalies(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        metric_timestamp=now,
        current_value=200.0,
        baseline_value=100.0,
        deviation_percent=100.0,
        severity=severity,
        detected_at=now,
        resolved_at=now if resolved else None,
        acknowledged_at=now if acknowledged else None,
        context={"seasonal": False},
    )
    db.add(a)
    db.flush()
    return a


def _make_insight(db: Session, tenant_id: uuid.UUID, anomaly: Anomalies) -> Insights:
    now = datetime.datetime.now(datetime.UTC)
    i = Insights(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        anomaly_id=anomaly.id,
        title="Spike detected",
        summary="page_view spiked 100%",
        explanation="Traffic surge explanation",
        confidence=0.9,
        created_at=now,
    )
    db.add(i)
    db.flush()
    return i


def _make_metric(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str,
    value: float = 100.0,
    minutes_ago: float = 30.0,
) -> Metrics:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    m = Metrics(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        metric_timestamp=ts,
        value=value,
        tags={},
        created_at=ts,
    )
    db.add(m)
    db.flush()
    return m


# ── /anomalies ─────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_list_anomalies_returns_open_by_default(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_anomaly(db, tenant_id, severity="high")
    _make_anomaly(db, tenant_id, severity="low", resolved=True)

    resp = client.get(f"/tenants/{tenant_id}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["severity"] == "high"
    assert data[0]["resolved_at"] is None


@pytest.mark.integration
def test_list_anomalies_status_resolved(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_anomaly(db, tenant_id)
    _make_anomaly(db, tenant_id, resolved=True)

    resp = client.get(f"/tenants/{tenant_id}/anomalies?status=resolved")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["resolved_at"] is not None


@pytest.mark.integration
def test_list_anomalies_status_all(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_anomaly(db, tenant_id)
    _make_anomaly(db, tenant_id, resolved=True)

    resp = client.get(f"/tenants/{tenant_id}/anomalies?status=all")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.integration
def test_list_anomalies_filter_by_severity(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_anomaly(db, tenant_id, severity="critical")
    _make_anomaly(db, tenant_id, severity="low")

    resp = client.get(f"/tenants/{tenant_id}/anomalies?status=all&severity=critical")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["severity"] == "critical"


@pytest.mark.integration
def test_list_anomalies_includes_insight(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)
    _make_insight(db, tenant_id, anomaly)

    resp = client.get(f"/tenants/{tenant_id}/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["insight"] is not None
    assert data[0]["insight"]["title"] == "Spike detected"


@pytest.mark.integration
def test_list_anomalies_pagination(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    for _ in range(5):
        _make_anomaly(db, tenant_id, severity="low")

    resp = client.get(f"/tenants/{tenant_id}/anomalies?status=all&limit=2&offset=0")
    assert len(resp.json()) == 2

    resp2 = client.get(f"/tenants/{tenant_id}/anomalies?status=all&limit=2&offset=2")
    assert len(resp2.json()) == 2


@pytest.mark.integration
def test_list_anomalies_unknown_tenant_returns_404(client: TestClient) -> None:
    resp = client.get(f"/tenants/{uuid.uuid4()}/anomalies")
    assert resp.status_code == 404


# ── /anomalies/{id} ────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_get_anomaly_returns_detail(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)
    insight = _make_insight(db, tenant_id, anomaly)

    resp = client.get(f"/tenants/{tenant_id}/anomalies/{anomaly.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(anomaly.id)
    assert data["insight"]["explanation"] == insight.explanation


@pytest.mark.integration
def test_get_anomaly_not_found(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    resp = client.get(f"/tenants/{tenant_id}/anomalies/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.integration
def test_get_anomaly_wrong_tenant_returns_404(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    other_id = uuid.uuid4()
    db.add(Tenants(id=other_id, name="other", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    anomaly = _make_anomaly(db, other_id)

    resp = client.get(f"/tenants/{tenant_id}/anomalies/{anomaly.id}")
    assert resp.status_code == 404


# ── /metrics ───────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_list_metrics_defaults_to_last_24h(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_metric(db, tenant_id, "event_count.page_view", minutes_ago=30)
    _make_metric(db, tenant_id, "event_count.page_view", minutes_ago=30 * 60)  # 30 hours ago

    resp = client.get(f"/tenants/{tenant_id}/metrics")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.integration
def test_list_metrics_filter_exact_name(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_metric(db, tenant_id, "event_count.page_view")
    _make_metric(db, tenant_id, "event_count.signup")

    resp = client.get(f"/tenants/{tenant_id}/metrics?metric_name=event_count.page_view")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["metric_name"] == "event_count.page_view"


@pytest.mark.integration
def test_list_metrics_filter_by_prefix(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_metric(db, tenant_id, "property.checkout.amount.avg")
    _make_metric(db, tenant_id, "property.checkout.amount.p95")
    _make_metric(db, tenant_id, "event_count.checkout")

    resp = client.get(f"/tenants/{tenant_id}/metrics?metric_name=property.checkout.")
    assert resp.status_code == 200
    names = {m["metric_name"] for m in resp.json()}
    assert names == {"property.checkout.amount.avg", "property.checkout.amount.p95"}


@pytest.mark.integration
def test_list_metrics_unknown_tenant_returns_404(client: TestClient) -> None:
    resp = client.get(f"/tenants/{uuid.uuid4()}/metrics")
    assert resp.status_code == 404


# ── /insights ──────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_list_insights_returns_most_recent_first(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    a1 = _make_anomaly(db, tenant_id)
    a2 = _make_anomaly(db, tenant_id)
    now = datetime.datetime.now(datetime.UTC)

    db.add(Insights(
        id=uuid.uuid4(), tenant_id=tenant_id, anomaly_id=a1.id,
        title="Older", summary="s", explanation="e", confidence=0.8,
        created_at=now - datetime.timedelta(hours=2),
    ))
    db.add(Insights(
        id=uuid.uuid4(), tenant_id=tenant_id, anomaly_id=a2.id,
        title="Newer", summary="s", explanation="e", confidence=0.9,
        created_at=now,
    ))
    db.flush()

    resp = client.get(f"/tenants/{tenant_id}/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["title"] == "Newer"
    assert data[1]["title"] == "Older"


@pytest.mark.integration
def test_list_insights_includes_anomaly(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    anomaly = _make_anomaly(db, tenant_id)
    _make_insight(db, tenant_id, anomaly)

    resp = client.get(f"/tenants/{tenant_id}/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["anomaly"] is not None
    assert data[0]["anomaly"]["metric_name"] == anomaly.metric_name


@pytest.mark.integration
def test_list_insights_unknown_tenant_returns_404(client: TestClient) -> None:
    resp = client.get(f"/tenants/{uuid.uuid4()}/insights")
    assert resp.status_code == 404


# ── /trends ────────────────────────────────────────────────────────────────────

def _make_trend(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str = "event_count.signup",
    direction: str = "up",
    resolved: bool = False,
) -> Trends:
    now = datetime.datetime.now(datetime.UTC)
    t = Trends(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        direction=direction,
        slope_per_hour=20.0 if direction == "up" else -20.0,
        change_percent_per_hour=14.3 if direction == "up" else -14.3,
        window_start=now - datetime.timedelta(hours=4),
        window_end=now,
        sample_size=5,
        mean_value=140.0,
        detected_at=now,
        resolved_at=now if resolved else None,
        context={"r_squared": 0.99},
    )
    db.add(t)
    db.flush()
    return t


@pytest.mark.integration
def test_list_trends_returns_open_by_default(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_trend(db, tenant_id, direction="up")
    _make_trend(db, tenant_id, metric_name="event_count.checkout", direction="down", resolved=True)

    resp = client.get(f"/tenants/{tenant_id}/trends")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "up"
    assert data[0]["resolved_at"] is None


@pytest.mark.integration
def test_list_trends_status_resolved(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_trend(db, tenant_id, direction="up")
    _make_trend(db, tenant_id, metric_name="event_count.checkout", resolved=True)

    resp = client.get(f"/tenants/{tenant_id}/trends?status=resolved")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["resolved_at"] is not None


@pytest.mark.integration
def test_list_trends_filter_by_direction(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_trend(db, tenant_id, metric_name="event_count.signup", direction="up")
    _make_trend(db, tenant_id, metric_name="event_count.checkout", direction="down")

    resp = client.get(f"/tenants/{tenant_id}/trends?status=all&direction=up")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["direction"] == "up"


@pytest.mark.integration
def test_list_trends_includes_context(client: TestClient, db: Session, tenant_id: uuid.UUID) -> None:
    _make_trend(db, tenant_id)

    resp = client.get(f"/tenants/{tenant_id}/trends")
    data = resp.json()
    assert data[0]["context"]["r_squared"] == 0.99


@pytest.mark.integration
def test_list_trends_unknown_tenant_returns_404(client: TestClient) -> None:
    resp = client.get(f"/tenants/{uuid.uuid4()}/trends")
    assert resp.status_code == 404
