"""
Integration tests for trend detection service.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid

import pytest
from sqlalchemy.orm import Session

from app.config import settings
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.services.trend import detect_trends


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
    minutes_ago: float,
) -> None:
    ts = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=minutes_ago)
    db.add(Metrics(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        metric_timestamp=ts,
        value=value,
        tags={},
        created_at=ts,
    ))
    db.flush()


def _insert_rising(db: Session, tenant_id: uuid.UUID, metric_name: str = "event_count.signup") -> None:
    """Insert a clearly rising series: 100, 120, 140, 160, 180 over 4 hours."""
    for i, val in enumerate([100.0, 120.0, 140.0, 160.0, 180.0]):
        _insert_metric(db, tenant_id, metric_name, val, minutes_ago=(240 - i * 60))


def _insert_falling(db: Session, tenant_id: uuid.UUID, metric_name: str = "event_count.signup") -> None:
    """Insert a clearly falling series: 200, 160, 120, 80, 40 over 4 hours."""
    for i, val in enumerate([200.0, 160.0, 120.0, 80.0, 40.0]):
        _insert_metric(db, tenant_id, metric_name, val, minutes_ago=(240 - i * 60))


@pytest.mark.integration
def test_no_metrics_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    assert detect_trends(db, tenant_id) == []


@pytest.mark.integration
def test_too_few_samples_skipped(db: Session, tenant_id: uuid.UUID) -> None:
    # Only 2 points — below trend_min_samples (3)
    _insert_metric(db, tenant_id, "event_count.page_view", 100.0, minutes_ago=60)
    _insert_metric(db, tenant_id, "event_count.page_view", 200.0, minutes_ago=30)

    assert detect_trends(db, tenant_id) == []


@pytest.mark.integration
def test_detects_rising_trend(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id)
    trends = detect_trends(db, tenant_id)

    assert len(trends) == 1
    assert trends[0].direction == "up"
    assert trends[0].slope_per_hour is not None
    assert trends[0].slope_per_hour > 0
    assert trends[0].change_percent_per_hour is not None
    assert trends[0].change_percent_per_hour > 0


@pytest.mark.integration
def test_detects_falling_trend(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_falling(db, tenant_id)
    trends = detect_trends(db, tenant_id)

    assert len(trends) == 1
    assert trends[0].direction == "down"
    assert trends[0].slope_per_hour is not None
    assert trends[0].slope_per_hour < 0


@pytest.mark.integration
def test_flat_series_not_flagged(db: Session, tenant_id: uuid.UUID) -> None:
    for i in range(5):
        _insert_metric(db, tenant_id, "event_count.checkout", 100.0, minutes_ago=(240 - i * 60))

    assert detect_trends(db, tenant_id) == []


@pytest.mark.integration
def test_trend_records_metric_name_and_sample_size(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id, "event_count.signup")
    trends = detect_trends(db, tenant_id)

    assert trends[0].metric_name == "event_count.signup"
    assert trends[0].sample_size == 5
    assert trends[0].mean_value == pytest.approx(140.0)


@pytest.mark.integration
def test_trend_stores_r_squared_in_context(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id)
    trends = detect_trends(db, tenant_id)

    ctx = trends[0].context
    assert ctx is not None
    assert "r_squared" in ctx
    # Perfect linear series should have r² close to 1
    assert ctx["r_squared"] == pytest.approx(1.0, abs=0.01)


@pytest.mark.integration
def test_open_trend_suppresses_duplicate(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id)
    first = detect_trends(db, tenant_id)
    assert len(first) == 1

    # Insert more rising data — same metric still has an open trend
    _insert_rising(db, tenant_id)
    second = detect_trends(db, tenant_id)
    assert second == []


@pytest.mark.integration
def test_resolved_trend_allows_new_detection(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id)
    first = detect_trends(db, tenant_id)
    assert len(first) == 1

    # Resolve the trend
    first[0].resolved_at = datetime.datetime.now(datetime.UTC)
    db.flush()

    _insert_rising(db, tenant_id)
    second = detect_trends(db, tenant_id)
    assert len(second) == 1


@pytest.mark.integration
def test_tenant_isolation(db: Session, tenant_id: uuid.UUID) -> None:
    other_id = uuid.uuid4()
    db.add(Tenants(id=other_id, name="other", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()

    _insert_rising(db, other_id)

    assert detect_trends(db, tenant_id) == []


@pytest.mark.integration
def test_metrics_outside_window_excluded(db: Session, tenant_id: uuid.UUID) -> None:
    beyond = settings.trend_window_hours * 60 + 30  # 30 min past the window
    for i, val in enumerate([100.0, 120.0, 140.0, 160.0, 180.0]):
        _insert_metric(db, tenant_id, "event_count.signup", val, minutes_ago=beyond + i * 10)

    assert detect_trends(db, tenant_id) == []


@pytest.mark.integration
def test_multiple_metrics_independent(db: Session, tenant_id: uuid.UUID) -> None:
    _insert_rising(db, tenant_id, "event_count.signup")
    _insert_falling(db, tenant_id, "event_count.checkout")

    trends = detect_trends(db, tenant_id)
    assert len(trends) == 2
    directions = {t.metric_name: t.direction for t in trends}
    assert directions["event_count.signup"] == "up"
    assert directions["event_count.checkout"] == "down"
