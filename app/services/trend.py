"""
Trend detection service.

For each metric name with enough data points in the trend window, fits a
linear regression over (timestamp, value) pairs and flags sustained directional
movement whose slope exceeds trend_change_threshold_pct % of the mean per hour.

Unlike anomaly detection — which compares a single window to a historical
baseline — trend detection asks whether the metric is consistently moving in
one direction over the recent trend_window_hours.

A Trends row is written when:
  1. The metric has >= trend_min_samples data points in the window.
  2. |change_percent_per_hour| >= trend_change_threshold_pct.
  3. No open trend (resolved_at IS NULL) already exists for this metric.

The context field stores r_squared (goodness of fit) so downstream consumers
can judge whether the trend is a clean signal or noisy.
"""
import datetime
import logging
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models.metric import Metrics
from app.models.trend import Trends

logger = logging.getLogger(__name__)


def detect_trends(db: Session, tenant_id: uuid.UUID) -> list[Trends]:
    """
    Run trend detection for all metric names for this tenant.
    Returns the list of Trends rows written.
    """
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    window_start = now - datetime.timedelta(hours=settings.trend_window_hours)

    rows = (
        db.query(Metrics)
        .filter(
            Metrics.tenant_id == tenant_id,
            Metrics.metric_timestamp >= window_start,
        )
        .order_by(Metrics.metric_name, Metrics.metric_timestamp)
        .all()
    )

    # Group by metric name
    by_name: dict[str, list[Metrics]] = {}
    for row in rows:
        if row.metric_name:
            by_name.setdefault(row.metric_name, []).append(row)

    # Pre-fetch open trends to avoid emitting duplicates
    open_trends: set[str | None] = {
        t.metric_name
        for t in db.query(Trends.metric_name)
        .filter(Trends.tenant_id == tenant_id, Trends.resolved_at == None)  # noqa: E711
        .all()
    }

    results: list[Trends] = []
    for metric_name, points in by_name.items():
        if len(points) < settings.trend_min_samples:
            continue
        if metric_name in open_trends:
            logger.debug("Skipping trend for %s — open trend already exists", metric_name)
            continue

        trend = _fit_trend(db, tenant_id, metric_name, points, now)
        if trend is not None:
            results.append(trend)

    db.flush()
    return results


def _fit_trend(
    db: Session,
    tenant_id: uuid.UUID,
    metric_name: str,
    points: list[Metrics],
    now: datetime.datetime,
) -> Trends | None:
    """
    Fit a linear regression over the data points. Returns a Trends row if the
    slope exceeds the configured threshold, otherwise None.

    x = minutes since the first point (avoids large timestamp numbers)
    y = metric value
    """
    origin = points[0].metric_timestamp
    if origin is None:
        return None

    xs: list[float] = []
    ys: list[float] = []
    for p in points:
        if p.metric_timestamp is None or p.value is None:
            continue
        delta = (p.metric_timestamp - origin).total_seconds() / 60.0
        xs.append(delta)
        ys.append(p.value)

    n = len(xs)
    if n < settings.trend_min_samples:
        return None

    # Ordinary least squares
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sum_xx - sum_x * sum_x

    if denom == 0:
        return None  # all timestamps identical — no slope

    slope_per_minute = (n * sum_xy - sum_x * sum_y) / denom
    slope_per_hour = slope_per_minute * 60.0
    mean_value = sum_y / n

    if mean_value == 0:
        return None  # can't compute % change relative to zero mean

    change_percent_per_hour = (slope_per_hour / mean_value) * 100.0

    if abs(change_percent_per_hour) < settings.trend_change_threshold_pct:
        return None

    # R² — goodness of fit
    y_mean = mean_value
    ss_tot = sum((y - y_mean) ** 2 for y in ys)
    if ss_tot == 0:
        r_squared = 1.0
    else:
        y_hat = [slope_per_minute * x + (sum_y - slope_per_minute * sum_x) / n for x in xs]
        ss_res = sum((y - yh) ** 2 for y, yh in zip(ys, y_hat))
        r_squared = max(0.0, 1.0 - ss_res / ss_tot)

    direction = "up" if slope_per_hour > 0 else "down"
    window_start = points[0].metric_timestamp
    window_end = points[-1].metric_timestamp

    logger.info(
        "Trend detected: %s %s %.1f%%/hr over %d samples (r²=%.2f)",
        metric_name, direction, abs(change_percent_per_hour), n, r_squared,
    )

    trend = Trends(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        metric_name=metric_name,
        direction=direction,
        slope_per_hour=slope_per_hour,
        change_percent_per_hour=change_percent_per_hour,
        window_start=window_start,
        window_end=window_end,
        sample_size=n,
        mean_value=mean_value,
        detected_at=now,
        context={"r_squared": round(r_squared, 4)},
    )
    db.add(trend)
    return trend
