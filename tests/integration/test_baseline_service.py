"""
Integration tests for baseline computation service.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.metric import MetricBaselines, Metrics
from app.models.tenant import Tenants
from app.services.baseline import compute_baselines


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _insert_metric(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str,
    value: float,
    timestamp: datetime.datetime | None = None,
) -> None:
    ts = timestamp or datetime.datetime.now(datetime.UTC)
    db.add(Metrics(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        metric_timestamp=ts,
        value=value,
        tags={},
        created_at=ts,
    ))


def _insert_metrics_same_slot(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str,
    values: list[float],
) -> None:
    """Insert metrics all pinned to the same weekday+hour slot (same weekday, one week apart)."""
    base = datetime.datetime.now(datetime.UTC).replace(minute=0, second=0, microsecond=0)
    for i, value in enumerate(values):
        _insert_metric(db, tenant_id, metric_name, value, base - datetime.timedelta(weeks=i))
    db.flush()


@pytest.mark.integration
def test_computes_avg_and_stddev(db: Session, tenant_id: uuid.UUID) -> None:
    # 4 values spaced 1 week apart — all land within the 28-day lookback window
    # and all in the same (weekday, hour) slot.  avg=15.0, stddev_samp≈2.582
    _insert_metrics_same_slot(db, tenant_id, "event_count.page_view", [12.0, 14.0, 16.0, 18.0])

    baselines = compute_baselines(db, tenant_id)

    # Both a seasonal slot and a global fallback should be written
    global_b = next(b for b in baselines if b.day_of_week is None)
    assert global_b.metric_name == "event_count.page_view"
    assert global_b.avg_value == pytest.approx(15.0)
    assert global_b.stddev == pytest.approx(2.582, rel=1e-2)
    assert global_b.sample_size == 4

    seasonal_b = next(b for b in baselines if b.day_of_week is not None)
    assert seasonal_b.metric_name == "event_count.page_view"
    assert seasonal_b.avg_value == pytest.approx(15.0)


@pytest.mark.integration
def test_skips_below_min_samples(db: Session, tenant_id: uuid.UUID) -> None:
    # Insert fewer than baseline_min_samples (default 4)
    _insert_metrics_same_slot(db, tenant_id, "event_count.page_view", [10.0] * 3)

    baselines = compute_baselines(db, tenant_id)

    assert baselines == []


@pytest.mark.integration
def test_skips_metrics_outside_lookback_window(db: Session, tenant_id: uuid.UUID) -> None:
    now = datetime.datetime.now(datetime.UTC)
    for i in range(10):
        _insert_metric(
            db, tenant_id, "event_count.page_view", 100.0,
            timestamp=now - datetime.timedelta(days=30 + i),
        )
    db.flush()

    baselines = compute_baselines(db, tenant_id)

    assert baselines == []


@pytest.mark.integration
def test_upserts_existing_global_baseline(db: Session, tenant_id: uuid.UUID) -> None:
    # Pre-insert a stale global baseline (day_of_week=None)
    db.add(MetricBaselines(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name="event_count.page_view",
        avg_value=999.0,
        stddev=999.0,
        sample_size=1,
        computed_at=datetime.datetime.now(datetime.UTC),
        tags={},
        day_of_week=None,
        hour_of_day=None,
    ))
    db.flush()

    _insert_metrics_same_slot(db, tenant_id, "event_count.page_view", [12.0, 14.0, 16.0, 18.0])
    baselines = compute_baselines(db, tenant_id)

    # Global baseline should be updated
    global_b = next(b for b in baselines if b.day_of_week is None)
    assert global_b.avg_value == pytest.approx(15.0)

    # Exactly one global row for this metric
    global_count = (
        db.query(MetricBaselines)
        .filter(
            MetricBaselines.tenant_id == tenant_id,
            MetricBaselines.day_of_week == None,  # noqa: E711
        )
        .count()
    )
    assert global_count == 1


@pytest.mark.integration
def test_no_metrics_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    baselines = compute_baselines(db, tenant_id)
    assert baselines == []


@pytest.mark.integration
def test_multiple_metric_names(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_metrics_same_slot(db, tenant_id, "event_count.page_view", [100.0] * 5)
    _insert_metrics_same_slot(db, tenant_id, "event_count.signup", [10.0] * 5)

    baselines = compute_baselines(db, tenant_id)

    names = {b.metric_name for b in baselines}
    assert names == {"event_count.page_view", "event_count.signup"}


@pytest.mark.integration
def test_seasonal_baselines_per_slot(db: Session, tenant_id: uuid.UUID) -> None:
    """Metrics spread across different weekday+hour slots produce separate seasonal baselines."""
    now = datetime.datetime.now(datetime.UTC).replace(minute=0, second=0, microsecond=0)

    # Insert 5 samples for Monday 09:00 UTC
    monday_9am = now - datetime.timedelta(days=(now.isoweekday() - 1) % 7, hours=now.hour - 9)
    if monday_9am > now:
        monday_9am -= datetime.timedelta(weeks=1)
    for i in range(5):
        _insert_metric(db, tenant_id, "event_count.checkout", 10.0,
                       monday_9am - datetime.timedelta(weeks=i))

    # Insert 5 samples for Wednesday 14:00 UTC
    wednesday_2pm = monday_9am + datetime.timedelta(days=2, hours=5)
    for i in range(5):
        _insert_metric(db, tenant_id, "event_count.checkout", 50.0,
                       wednesday_2pm - datetime.timedelta(weeks=i))
    db.flush()

    baselines = compute_baselines(db, tenant_id)

    seasonal = [b for b in baselines if b.day_of_week is not None]
    slots = {(b.day_of_week, b.hour_of_day) for b in seasonal}
    assert len(slots) == 2

    mon_b = next(b for b in seasonal if b.day_of_week == 1)
    assert mon_b.avg_value == pytest.approx(10.0)

    wed_b = next(b for b in seasonal if b.day_of_week == 3)
    assert wed_b.avg_value == pytest.approx(50.0)


@pytest.mark.integration
def test_seasonal_and_global_both_written(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_metrics_same_slot(db, tenant_id, "event_count.page_view", [100.0] * 5)

    baselines = compute_baselines(db, tenant_id)

    seasonal = [b for b in baselines if b.day_of_week is not None]
    global_ = [b for b in baselines if b.day_of_week is None]
    assert len(seasonal) == 1
    assert len(global_) == 1
