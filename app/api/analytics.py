"""
Read API for anomalies, metrics, and insights.

All routes are scoped to a tenant via the URL path. Query params allow
lightweight filtering without requiring a full query language.
"""
import datetime
import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, selectinload

from app.database.session import get_db
from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.models.trend import Trends
from app.schemas.analytics import (
    AnomalyDetailResponse,
    AnomalyResponse,
    InsightResponse,
    MetricResponse,
    TrendResponse,
)

router = APIRouter(prefix="/tenants/{tenant_id}", tags=["analytics"])

_DEFAULT_METRICS_HOURS = 24
_MAX_LIMIT = 200


class AnomalyStatus(StrEnum):
    open = "open"
    acknowledged = "acknowledged"
    resolved = "resolved"
    all = "all"


def _require_tenant(tenant_id: uuid.UUID, db: Session) -> None:
    if not db.query(Tenants.id).filter(Tenants.id == tenant_id).first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")


# ── Anomalies ──────────────────────────────────────────────────────────────────

@router.get("/anomalies", response_model=list[AnomalyResponse])
def list_anomalies(
    tenant_id: uuid.UUID,
    status: AnomalyStatus = AnomalyStatus.open,
    severity: Annotated[str | None, Query(description="Filter by severity: low, medium, high, critical")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
) -> list[Anomalies]:
    _require_tenant(tenant_id, db)

    q = (
        db.query(Anomalies)
        .options(selectinload(Anomalies.insights))
        .filter(Anomalies.tenant_id == tenant_id)
        .order_by(Anomalies.detected_at.desc())
    )

    if status == AnomalyStatus.open:
        q = q.filter(Anomalies.resolved_at == None)  # noqa: E711
    elif status == AnomalyStatus.acknowledged:
        q = q.filter(Anomalies.acknowledged_at != None, Anomalies.resolved_at == None)  # noqa: E711
    elif status == AnomalyStatus.resolved:
        q = q.filter(Anomalies.resolved_at != None)  # noqa: E711

    if severity:
        q = q.filter(Anomalies.severity == severity)

    rows = q.offset(offset).limit(limit).all()

    # Attach the most recent insight to each anomaly for convenience
    results: list[Anomalies] = []
    for anomaly in rows:
        anomaly.insight = anomaly.insights[-1] if anomaly.insights else None  # type: ignore[attr-defined]
        results.append(anomaly)
    return results


@router.get("/anomalies/{anomaly_id}", response_model=AnomalyDetailResponse)
def get_anomaly(
    tenant_id: uuid.UUID,
    anomaly_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> Anomalies:
    _require_tenant(tenant_id, db)

    anomaly = (
        db.query(Anomalies)
        .options(selectinload(Anomalies.insights))
        .filter(Anomalies.tenant_id == tenant_id, Anomalies.id == anomaly_id)
        .first()
    )
    if anomaly is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anomaly not found")

    anomaly.insight = anomaly.insights[-1] if anomaly.insights else None  # type: ignore[attr-defined]
    return anomaly


# ── Metrics ────────────────────────────────────────────────────────────────────

@router.get("/metrics", response_model=list[MetricResponse])
def list_metrics(
    tenant_id: uuid.UUID,
    metric_name: Annotated[str | None, Query(description="Exact metric name or prefix (e.g. 'event_count.' or 'property.checkout.')")] = None,
    since: Annotated[datetime.datetime | None, Query(description="ISO 8601 timestamp; defaults to 24 h ago")] = None,
    until: Annotated[datetime.datetime | None, Query(description="ISO 8601 timestamp; defaults to now")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 200,
    db: Session = Depends(get_db),
) -> list[Metrics]:
    _require_tenant(tenant_id, db)

    now = datetime.datetime.now(datetime.UTC)
    since = since or (now - datetime.timedelta(hours=_DEFAULT_METRICS_HOURS))
    until = until or now

    q = (
        db.query(Metrics)
        .filter(
            Metrics.tenant_id == tenant_id,
            Metrics.metric_timestamp >= since,
            Metrics.metric_timestamp <= until,
        )
        .order_by(Metrics.metric_timestamp.desc())
    )

    if metric_name:
        if metric_name.endswith(".") or not metric_name.replace(".", "").replace("_", "").isalnum():
            # Treat as prefix
            q = q.filter(Metrics.metric_name.like(f"{metric_name}%"))
        else:
            # First try exact; if caller included a trailing wildcard-style prefix, also match prefix
            q = q.filter(
                (Metrics.metric_name == metric_name) | Metrics.metric_name.like(f"{metric_name}.%")
            )

    return q.limit(limit).all()


# ── Trends ────────────────────────────────────────────────────────────────────

class TrendStatus(StrEnum):
    open = "open"
    resolved = "resolved"
    all = "all"


@router.get("/trends", response_model=list[TrendResponse])
def list_trends(
    tenant_id: uuid.UUID,
    status: TrendStatus = TrendStatus.open,
    direction: Annotated[str | None, Query(description="Filter by direction: up or down")] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
) -> list[Trends]:
    _require_tenant(tenant_id, db)

    q = (
        db.query(Trends)
        .filter(Trends.tenant_id == tenant_id)
        .order_by(Trends.detected_at.desc())
    )

    if status == TrendStatus.open:
        q = q.filter(Trends.resolved_at == None)  # noqa: E711
    elif status == TrendStatus.resolved:
        q = q.filter(Trends.resolved_at != None)  # noqa: E711

    if direction:
        q = q.filter(Trends.direction == direction)

    return q.offset(offset).limit(limit).all()


# ── Insights ───────────────────────────────────────────────────────────────────

@router.get("/insights", response_model=list[InsightResponse])
def list_insights(
    tenant_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Session = Depends(get_db),
) -> list[Insights]:
    _require_tenant(tenant_id, db)

    return (
        db.query(Insights)
        .options(selectinload(Insights.anomaly))
        .filter(Insights.tenant_id == tenant_id)
        .order_by(Insights.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
