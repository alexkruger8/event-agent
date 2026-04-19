import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.insight import Insights


def get_recent_insights(db: Session, tenant_id: str, limit: int) -> list[dict[str, Any]]:
    """Return recent LLM-generated insights with summaries and confidence scores."""
    tid = uuid.UUID(tenant_id)

    rows = (
        db.query(Insights)
        .filter(Insights.tenant_id == tid)
        .order_by(Insights.created_at.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": str(i.id),
            "title": i.title,
            "summary": i.summary,
            "confidence": i.confidence,
            "anomaly_id": str(i.anomaly_id) if i.anomaly_id else None,
            "trend_id": str(i.trend_id) if i.trend_id else None,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
        for i in rows
    ]
