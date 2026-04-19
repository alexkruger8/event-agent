"""
Integration tests for anomaly detection service.
Requires a running database (docker compose up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.metric import MetricBaselines, Metrics
from app.models.tenant import Tenants
from app.services.anomaly import detect_anomalies


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_metric(db: Session, tenant_id: uuid.UUID, metric_name: str, value: float) -> Metrics:
    now = datetime.datetime.now(datetime.UTC)
    m = Metrics(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        metric_timestamp=now,
        value=value,
        tags={},
        created_at=now,
    )
    db.add(m)
    db.flush()
    return m


def _make_baseline(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str,
    avg_value: float,
    stddev: float,
    sample_size: int = 100,
) -> MetricBaselines:
    b = MetricBaselines(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        avg_value=avg_value,
        stddev=stddev,
        sample_size=sample_size,
        computed_at=datetime.datetime.now(datetime.UTC),
    )
    db.add(b)
    db.flush()
    return b


@pytest.mark.integration
def test_no_anomaly_within_threshold(db: Session, tenant_id: uuid.UUID) -> None:
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)
    metric = _make_metric(db, tenant_id, "event_count.page_view", value=115.0)  # 1.5 stddevs

    anomalies = detect_anomalies(db, [metric])
    assert anomalies == []


@pytest.mark.integration
def test_anomaly_detected_above_threshold(db: Session, tenant_id: uuid.UUID) -> None:
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)
    metric = _make_metric(db, tenant_id, "event_count.page_view", value=145.0)  # 4.5 stddevs

    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1
    assert anomalies[0].severity == "high"
    assert anomalies[0].current_value == 145.0
    assert anomalies[0].baseline_value == 100.0
    assert anomalies[0].metric_id == metric.id


@pytest.mark.integration
def test_anomaly_detected_below_threshold(db: Session, tenant_id: uuid.UUID) -> None:
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)
    metric = _make_metric(db, tenant_id, "event_count.page_view", value=55.0)  # -4.5 stddevs

    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1
    assert anomalies[0].severity == "high"
    assert anomalies[0].deviation_percent == pytest.approx(-45.0)


@pytest.mark.integration
def test_skips_metric_with_no_baseline(db: Session, tenant_id: uuid.UUID) -> None:
    metric = _make_metric(db, tenant_id, "event_count.page_view", value=999.0)

    anomalies = detect_anomalies(db, [metric])
    assert anomalies == []


@pytest.mark.integration
def test_empty_metrics_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    anomalies = detect_anomalies(db, [])
    assert anomalies == []


@pytest.mark.integration
def test_multiple_metrics_only_flags_outliers(db: Session, tenant_id: uuid.UUID) -> None:
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)
    _make_baseline(db, tenant_id, "event_count.signup", avg_value=10.0, stddev=1.0)

    normal = _make_metric(db, tenant_id, "event_count.page_view", value=105.0)   # 0.5 stddevs
    outlier = _make_metric(db, tenant_id, "event_count.signup", value=50.0)      # 40 stddevs

    anomalies = detect_anomalies(db, [normal, outlier])
    assert len(anomalies) == 1
    assert anomalies[0].metric_id == outlier.id
    assert anomalies[0].severity == "critical"


@pytest.mark.integration
def test_seasonal_baseline_preferred_over_global(db: Session, tenant_id: uuid.UUID) -> None:
    """When both a seasonal and global baseline exist, the seasonal one is used."""
    now = datetime.datetime.now(datetime.UTC)
    dow = now.isoweekday()
    hod = now.hour

    # Global baseline says value=200 is normal
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=200.0, stddev=5.0)

    # Seasonal baseline for the current slot says value=200 is an anomaly
    seasonal = MetricBaselines(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name="event_count.page_view",
        day_of_week=dow,
        hour_of_day=hod,
        avg_value=100.0,
        stddev=5.0,
        sample_size=10,
        computed_at=now,
    )
    db.add(seasonal)
    db.flush()

    metric = _make_metric(db, tenant_id, "event_count.page_view", value=200.0)  # 20 stddevs above seasonal

    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1
    ctx = anomalies[0].context
    assert ctx is not None
    assert ctx["seasonal"] is True
    assert anomalies[0].baseline_value == 100.0


@pytest.mark.integration
def test_falls_back_to_global_when_no_seasonal(db: Session, tenant_id: uuid.UUID) -> None:
    """When no seasonal baseline exists for the current slot, global baseline is used."""
    # Only a global baseline (no dow/hod)
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=5.0)

    metric = _make_metric(db, tenant_id, "event_count.page_view", value=135.0)  # 7 stddevs

    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1
    ctx = anomalies[0].context
    assert ctx is not None
    assert ctx["seasonal"] is False


@pytest.mark.integration
def test_anomaly_context_records_seasonal_flag(db: Session, tenant_id: uuid.UUID) -> None:
    """context.seasonal is False when falling back to a global baseline."""
    _make_baseline(db, tenant_id, "event_count.checkout", avg_value=10.0, stddev=1.0)
    metric = _make_metric(db, tenant_id, "event_count.checkout", value=50.0)

    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1
    ctx = anomalies[0].context
    assert ctx is not None
    assert "seasonal" in ctx
    assert ctx["seasonal"] is False


# ── Deduplication ─────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_dedup_suppresses_repeat_anomaly_within_cooldown(db: Session, tenant_id: uuid.UUID) -> None:
    """A second anomaly for the same metric within the cooldown window is suppressed."""
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)

    # First detection — should fire
    metric1 = _make_metric(db, tenant_id, "event_count.page_view", value=145.0)
    first = detect_anomalies(db, [metric1])
    assert len(first) == 1

    # Second detection for the same metric shortly after — should be suppressed
    metric2 = _make_metric(db, tenant_id, "event_count.page_view", value=160.0)
    second = detect_anomalies(db, [metric2])
    assert second == []


@pytest.mark.integration
def test_dedup_fires_again_after_resolution(db: Session, tenant_id: uuid.UUID) -> None:
    """Once an anomaly is resolved, the next detection should fire a new one."""
    now = datetime.datetime.now(datetime.UTC)
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)

    # Pre-insert an already-resolved anomaly
    resolved = Anomalies(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name="event_count.page_view",
        metric_timestamp=now,
        current_value=145.0,
        baseline_value=100.0,
        deviation_percent=45.0,
        severity="high",
        detected_at=now - datetime.timedelta(hours=1),
        resolved_at=now - datetime.timedelta(minutes=30),
        context={},
    )
    db.add(resolved)
    db.flush()

    metric = _make_metric(db, tenant_id, "event_count.page_view", value=150.0)
    anomalies = detect_anomalies(db, [metric])
    assert len(anomalies) == 1


@pytest.mark.integration
def test_dedup_independent_per_metric(db: Session, tenant_id: uuid.UUID) -> None:
    """Cooldown for one metric does not suppress anomalies on a different metric."""
    _make_baseline(db, tenant_id, "event_count.page_view", avg_value=100.0, stddev=10.0)
    _make_baseline(db, tenant_id, "event_count.signup", avg_value=10.0, stddev=1.0)

    metric1 = _make_metric(db, tenant_id, "event_count.page_view", value=145.0)
    detect_anomalies(db, [metric1])  # puts page_view in cooldown

    # signup anomaly should still fire
    metric2 = _make_metric(db, tenant_id, "event_count.signup", value=50.0)
    anomalies = detect_anomalies(db, [metric2])
    assert len(anomalies) == 1
    assert anomalies[0].metric_name == "event_count.signup"
