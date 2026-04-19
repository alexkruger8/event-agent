import datetime
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.metric import MetricBaselines, Metrics


def compute_baselines(db: Session, tenant_id: uuid.UUID) -> list[MetricBaselines]:
    """
    Compute seasonality-aware baselines for each (metric_name, day_of_week, hour_of_day)
    slot, plus a global fallback baseline (NULL day/hour) for each metric.

    Day-of-week uses ISO 8601: 1=Monday … 7=Sunday, matching Python's isoweekday()
    and PostgreSQL's EXTRACT(ISODOW …). All timestamps are treated as UTC.

    A seasonal slot is written only when it has >= baseline_min_samples data points
    (typically ~4–5 per slot over a 28-day lookback). The global fallback is written
    when the metric has >= baseline_min_samples total points, giving new tenants some
    protection before per-slot data accumulates.

    Returns every MetricBaselines row written or updated.
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    cutoff = now - datetime.timedelta(days=settings.baseline_lookback_days)
    results: list[MetricBaselines] = []

    base_filter = [
        Metrics.tenant_id == tenant_id,
        Metrics.metric_timestamp >= cutoff,
    ]

    # ── Seasonal baselines (one per metric × weekday × hour) ─────────────────
    seasonal_rows = (
        db.query(
            Metrics.metric_name,
            func.extract("isodow", Metrics.metric_timestamp).label("day_of_week"),
            func.extract("hour", Metrics.metric_timestamp).label("hour_of_day"),
            func.avg(Metrics.value).label("avg_value"),
            func.stddev_samp(Metrics.value).label("stddev"),
            func.count().label("sample_size"),
        )
        .filter(*base_filter)
        .group_by(
            Metrics.metric_name,
            func.extract("isodow", Metrics.metric_timestamp),
            func.extract("hour", Metrics.metric_timestamp),
        )
        .all()
    )

    for row in seasonal_rows:
        if row.sample_size < settings.baseline_min_samples:
            continue
        results.append(
            _upsert(db, tenant_id, row, now, int(row.day_of_week), int(row.hour_of_day))
        )

    # ── Global fallback baselines (NULL day/hour) ─────────────────────────────
    global_rows = (
        db.query(
            Metrics.metric_name,
            func.avg(Metrics.value).label("avg_value"),
            func.stddev_samp(Metrics.value).label("stddev"),
            func.count().label("sample_size"),
        )
        .filter(*base_filter)
        .group_by(Metrics.metric_name)
        .all()
    )

    for row in global_rows:
        if row.sample_size < settings.baseline_min_samples:
            continue
        results.append(_upsert(db, tenant_id, row, now, None, None))

    db.flush()
    return results


def _upsert(
    db: Session,
    tenant_id: uuid.UUID,
    row: Any,
    now: datetime.datetime,
    day_of_week: int | None,
    hour_of_day: int | None,
) -> MetricBaselines:
    baseline = (
        db.query(MetricBaselines)
        .filter(
            MetricBaselines.tenant_id == tenant_id,
            MetricBaselines.metric_name == row.metric_name,
            MetricBaselines.day_of_week == day_of_week,
            MetricBaselines.hour_of_day == hour_of_day,
        )
        .first()
    )

    if baseline is None:
        baseline = MetricBaselines(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            metric_name=row.metric_name,
            day_of_week=day_of_week,
            hour_of_day=hour_of_day,
            tags={},
        )
        db.add(baseline)

    baseline.avg_value = float(row.avg_value)
    baseline.stddev = float(row.stddev) if row.stddev is not None else 0.0
    baseline.sample_size = row.sample_size
    baseline.computed_at = now
    return baseline
