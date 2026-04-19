import datetime
import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.metric import Metrics


def search_metric_names(db: Session, tenant_id: str, keyword: str) -> list[dict[str, Any]]:
    """Search available metric names by keyword, with sample counts and latest timestamp."""
    tid = uuid.UUID(tenant_id)

    rows = (
        db.query(
            Metrics.metric_name,
            func.count(Metrics.id).label("sample_count"),
            func.max(Metrics.metric_timestamp).label("latest"),
        )
        .filter(Metrics.tenant_id == tid, Metrics.metric_name.ilike(f"%{keyword}%"))
        .group_by(Metrics.metric_name)
        .order_by(func.max(Metrics.metric_timestamp).desc())
        .limit(20)
        .all()
    )

    return [
        {
            "metric_name": row.metric_name,
            "sample_count": row.sample_count,
            "latest_timestamp": row.latest.isoformat() if row.latest else None,
        }
        for row in rows
    ]


def get_metric_summary(
    db: Session,
    tenant_id: str,
    metric_name: str,
    hours: int,
) -> dict[str, Any]:
    """Return a statistical summary for a metric over a recent time window."""
    tid = uuid.UUID(tenant_id)
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)

    rows = (
        db.query(Metrics)
        .filter(
            Metrics.tenant_id == tid,
            Metrics.metric_name == metric_name,
            Metrics.metric_timestamp >= since,
        )
        .order_by(Metrics.metric_timestamp.asc())
        .all()
    )

    values = [r.value for r in rows if r.value is not None]
    if not values:
        return {
            "metric_name": metric_name,
            "window_hours": hours,
            "sample_count": 0,
            "min": None,
            "max": None,
            "avg": None,
            "latest_value": None,
            "latest_timestamp": None,
            "active_anomaly": None,
        }

    active_anomaly = (
        db.query(Anomalies)
        .filter(
            Anomalies.tenant_id == tid,
            Anomalies.metric_name == metric_name,
            Anomalies.resolved_at.is_(None),
        )
        .order_by(Anomalies.detected_at.desc())
        .first()
    )

    latest = rows[-1]
    return {
        "metric_name": metric_name,
        "window_hours": hours,
        "sample_count": len(values),
        "min": min(values),
        "max": max(values),
        "avg": sum(values) / len(values),
        "latest_value": latest.value,
        "latest_timestamp": latest.metric_timestamp.isoformat() if latest.metric_timestamp else None,
        "active_anomaly": {
            "id": str(active_anomaly.id),
            "severity": active_anomaly.severity,
            "deviation_percent": active_anomaly.deviation_percent,
            "detected_at": active_anomaly.detected_at.isoformat() if active_anomaly.detected_at else None,
        } if active_anomaly else None,
    }
