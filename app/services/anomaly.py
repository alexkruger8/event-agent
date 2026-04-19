import datetime
import logging
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.models.anomaly import Anomalies
from app.models.metric import MetricBaselines, Metrics

logger = logging.getLogger(__name__)


def _severity(deviations: float) -> str:
    abs_dev = abs(deviations)
    if abs_dev >= 5:
        return "critical"
    if abs_dev >= 4:
        return "high"
    if abs_dev >= 3:
        return "medium"
    return "low"


def detect_anomalies(db: Session, metrics: list[Metrics]) -> list[Anomalies]:
    """
    Compare each metric against its baseline. Prefers a seasonality-aware baseline
    (matched by day_of_week + hour_of_day) and falls back to the global baseline
    (NULL day/hour) when no seasonal slot exists.

    Writes an Anomalies row for any metric that deviates more than
    anomaly_threshold_stddev standard deviations from the matched baseline mean.

    Returns the list of Anomalies rows written.
    """
    if not metrics:
        return []

    tenant_id = metrics[0].tenant_id
    metric_names = list({m.metric_name for m in metrics})

    all_baselines = (
        db.query(MetricBaselines)
        .filter(
            MetricBaselines.tenant_id == tenant_id,
            MetricBaselines.metric_name.in_(metric_names),
        )
        .all()
    )

    # Two lookup maps; seasonal takes priority over global fallback
    seasonal_map: dict[tuple[str | None, int, int], MetricBaselines] = {}
    global_map: dict[str | None, MetricBaselines] = {}

    for b in all_baselines:
        if b.day_of_week is not None and b.hour_of_day is not None:
            seasonal_map[(b.metric_name, b.day_of_week, b.hour_of_day)] = b
        else:
            global_map[b.metric_name] = b

    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    cooldown_cutoff = now - datetime.timedelta(hours=settings.anomaly_cooldown_hours)

    # Pre-fetch open anomalies within the cooldown window to avoid N+1 queries
    open_anomalies = (
        db.query(Anomalies.metric_name)
        .filter(
            Anomalies.tenant_id == tenant_id,
            Anomalies.metric_name.in_(metric_names),
            Anomalies.resolved_at == None,  # noqa: E711
            Anomalies.detected_at >= cooldown_cutoff,
        )
        .distinct()
        .all()
    )
    in_cooldown: set[str | None] = {row.metric_name for row in open_anomalies}

    anomalies: list[Anomalies] = []

    for metric in metrics:
        ts = metric.metric_timestamp or now
        dow = ts.isoweekday()   # 1=Mon … 7=Sun — matches PostgreSQL EXTRACT(ISODOW)
        hod = ts.hour

        baseline = seasonal_map.get((metric.metric_name, dow, hod))
        using_seasonal = baseline is not None
        if baseline is None:
            baseline = global_map.get(metric.metric_name)

        if baseline is None or baseline.stddev is None or baseline.avg_value is None:
            continue

        if metric.metric_name in in_cooldown:
            logger.debug(
                "Skipping anomaly for %s — unresolved anomaly within %dh cooldown",
                metric.metric_name, settings.anomaly_cooldown_hours,
            )
            continue

        if baseline.stddev == 0:
            if metric.value != baseline.avg_value:
                deviations = float("inf")
            else:
                continue
        else:
            deviations = (metric.value - baseline.avg_value) / baseline.stddev  # type: ignore[operator]

        if abs(deviations) < settings.anomaly_threshold_stddev:
            continue

        deviation_percent = (
            ((metric.value - baseline.avg_value) / baseline.avg_value * 100)  # type: ignore[operator]
            if baseline.avg_value != 0
            else None
        )

        anomaly = Anomalies(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            metric_id=metric.id,
            metric_name=metric.metric_name,
            metric_timestamp=metric.metric_timestamp,
            current_value=metric.value,
            baseline_value=baseline.avg_value,
            deviation_percent=deviation_percent,
            severity=_severity(deviations),
            detected_at=now,
            context={
                "stddev": baseline.stddev,
                "sample_size": baseline.sample_size,
                "deviations_from_mean": deviations if deviations != float("inf") else None,
                "seasonal": using_seasonal,
            },
        )
        db.add(anomaly)
        anomalies.append(anomaly)

    db.flush()
    return anomalies
