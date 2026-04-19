import datetime
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import Events
from app.models.metric import Metrics


def compute_metrics(db: Session, tenant_id: uuid.UUID) -> list[Metrics]:
    """
    Count events per event_name within the last metric_window_minutes for the
    given tenant and persist one Metrics row per distinct event name.

    Returns the list of Metrics rows written.
    """
    _now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    window_start = _now - datetime.timedelta(minutes=settings.metric_window_minutes)
    metric_timestamp = _now

    counts = (
        db.query(Events.event_name, func.count().label("cnt"))
        .filter(
            Events.tenant_id == tenant_id,
            Events.timestamp >= window_start,
        )
        .group_by(Events.event_name)
        .all()
    )

    metrics: list[Metrics] = []
    for event_name, count in counts:
        metric = Metrics(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            metric_name=f"event_count.{event_name}",
            metric_timestamp=metric_timestamp,
            value=float(count),
            tags={"event_name": event_name},
            created_at=metric_timestamp,
        )
        db.add(metric)
        metrics.append(metric)

    db.flush()
    return metrics
