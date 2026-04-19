import datetime
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.error import Errors
from app.models.insight import Insights
from app.models.trend import Trends


def get_system_health_summary(db: Session, tenant_id: str) -> dict[str, Any]:
    """Return a bird's-eye view of production health for a tenant."""
    tid = uuid.UUID(tenant_id)
    now = datetime.datetime.now(datetime.UTC)

    # Open anomalies grouped by severity
    anomaly_rows = (
        db.query(Anomalies.severity)
        .filter(Anomalies.tenant_id == tid, Anomalies.resolved_at.is_(None))
        .all()
    )
    anomalies_by_severity: dict[str, int] = {}
    for (sev,) in anomaly_rows:
        key = sev or "unknown"
        anomalies_by_severity[key] = anomalies_by_severity.get(key, 0) + 1

    # Unresolved errors grouped by service
    error_rows = (
        db.query(Errors.service)
        .filter(Errors.tenant_id == tid, Errors.resolved_at.is_(None))
        .all()
    )
    errors_by_service: dict[str, int] = {}
    for (svc,) in error_rows:
        key = svc or "unknown"
        errors_by_service[key] = errors_by_service.get(key, 0) + 1

    # Active trends (resolved_at IS NULL, detected in last 24h)
    since = now - datetime.timedelta(hours=24)
    trends = (
        db.query(Trends)
        .filter(
            Trends.tenant_id == tid,
            Trends.resolved_at.is_(None),
            Trends.detected_at >= since,
        )
        .order_by(Trends.detected_at.desc())
        .limit(10)
        .all()
    )
    active_trends = [
        {
            "metric_name": t.metric_name,
            "direction": t.direction,
            "change_pct_per_hour": t.change_percent_per_hour,
        }
        for t in trends
    ]

    # Latest insight title
    latest_insight = (
        db.query(Insights)
        .filter(Insights.tenant_id == tid)
        .order_by(Insights.created_at.desc())
        .first()
    )

    return {
        "open_anomalies_by_severity": anomalies_by_severity,
        "unresolved_errors_by_service": errors_by_service,
        "active_trends": active_trends,
        "latest_insight_title": latest_insight.title if latest_insight else None,
        "as_of": now.isoformat(),
    }
