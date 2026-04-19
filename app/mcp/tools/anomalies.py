import datetime
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.insight import Insights


def get_recent_anomalies(
    db: Session,
    tenant_id: str,
    severity: str | None,
    hours: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return recent unresolved anomalies, optionally filtered by severity."""
    tid = uuid.UUID(tenant_id)
    since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)

    q = db.query(Anomalies).filter(
        Anomalies.tenant_id == tid,
        Anomalies.resolved_at.is_(None),
        Anomalies.detected_at >= since,
    )
    if severity is not None:
        q = q.filter(Anomalies.severity == severity)

    rows = q.order_by(Anomalies.detected_at.desc()).limit(limit).all()

    results = []
    for a in rows:
        # Fetch the most recent insight for this anomaly
        insight = (
            db.query(Insights)
            .filter(Insights.anomaly_id == a.id)
            .order_by(Insights.created_at.desc())
            .first()
        )
        results.append({
            "id": str(a.id),
            "metric_name": a.metric_name,
            "severity": a.severity,
            "current_value": a.current_value,
            "baseline_value": a.baseline_value,
            "deviation_percent": a.deviation_percent,
            "detected_at": a.detected_at.isoformat() if a.detected_at else None,
            "insight_summary": insight.summary if insight else None,
        })
    return results


def get_anomaly_detail(db: Session, tenant_id: str, anomaly_id: str) -> dict[str, Any] | None:
    """Return full detail for a single anomaly including latest insight."""
    tid = uuid.UUID(tenant_id)
    aid = uuid.UUID(anomaly_id)

    a = db.query(Anomalies).filter(Anomalies.tenant_id == tid, Anomalies.id == aid).first()
    if a is None:
        return None

    insight = (
        db.query(Insights)
        .filter(Insights.anomaly_id == a.id)
        .order_by(Insights.created_at.desc())
        .first()
    )

    return {
        "id": str(a.id),
        "metric_name": a.metric_name,
        "metric_timestamp": a.metric_timestamp.isoformat() if a.metric_timestamp else None,
        "severity": a.severity,
        "current_value": a.current_value,
        "baseline_value": a.baseline_value,
        "deviation_percent": a.deviation_percent,
        "detected_at": a.detected_at.isoformat() if a.detected_at else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "context": a.context,
        "insight": {
            "id": str(insight.id),
            "title": insight.title,
            "summary": insight.summary,
            "explanation": insight.explanation,
            "confidence": insight.confidence,
            "created_at": insight.created_at.isoformat() if insight.created_at else None,
        } if insight else None,
    }
