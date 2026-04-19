"""
Property metric computation.

For each event type with tracked_properties in its metadata, computes avg and p95
of those numeric properties within the current metric window and writes them as
Metrics rows with names of the form:

    property.{event_name}.{property_key}.avg
    property.{event_name}.{property_key}.p95

This plugs into the existing baseline + anomaly detection pipeline unchanged —
a property metric is just another metric name as far as downstream services care.

Tracked properties are configured per event type via the conversational agent
(update_tracked_properties tool) and stored as:

    event_types.metadata["tracked_properties"] = {"amount": ["avg", "p95"], ...}
"""
import datetime
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.event import EventTypes
from app.models.metric import Metrics

logger = logging.getLogger(__name__)

# Property must be present and numeric on at least this fraction of events in
# the window before we write a metric. Prevents noisy/misleading averages from
# very sparse properties.
MIN_PRESENCE_RATE = 0.3

# Regex used inside Postgres to identify numeric string values.
_NUMERIC_RE = r"^-?[0-9]+\.?[0-9]*$"


def compute_property_metrics(db: Session, tenant_id: uuid.UUID) -> list[Metrics]:
    """
    Compute property-level metrics for all tracked properties across all event
    types for this tenant. Returns the list of Metrics rows written.
    """
    _now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    window_start = _now - datetime.timedelta(minutes=settings.metric_window_minutes)
    metric_timestamp = _now

    event_types = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id)
        .all()
    )

    metrics: list[Metrics] = []

    for et in event_types:
        if not et.type_metadata:
            continue
        tracked: dict[str, list[str]] = et.type_metadata.get("tracked_properties") or {}
        if not tracked or not et.event_name:
            continue

        for prop_key, aggregations in tracked.items():
            rows = _compute_property(
                db, tenant_id, et.event_name, prop_key, aggregations,
                window_start, metric_timestamp,
            )
            metrics.extend(rows)

    db.flush()
    return metrics


def _compute_property(
    db: Session,
    tenant_id: uuid.UUID,
    event_name: str,
    prop_key: str,
    aggregations: list[str],
    window_start: datetime.datetime,
    metric_timestamp: datetime.datetime,
) -> list[Metrics]:
    """Run one SQL query to get all aggregations for a single (event_name, property_key) pair."""
    row = db.execute(
        text("""
            SELECT
                COUNT(*) AS total_count,
                COUNT(*) FILTER (
                    WHERE properties->>:prop ~ :numeric_re
                ) AS present_count,
                AVG(
                    CASE WHEN properties->>:prop ~ :numeric_re
                         THEN (properties->>:prop)::double precision END
                ) AS avg_val,
                PERCENTILE_CONT(0.95) WITHIN GROUP (
                    ORDER BY (properties->>:prop)::double precision
                ) FILTER (
                    WHERE properties->>:prop ~ :numeric_re
                ) AS p95_val
            FROM events
            WHERE tenant_id     = :tenant_id
              AND event_name     = :event_name
              AND timestamp     >= :window_start
        """),
        {
            "prop": prop_key,
            "numeric_re": _NUMERIC_RE,
            "tenant_id": str(tenant_id),
            "event_name": event_name,
            "window_start": window_start,
        },
    ).first()

    if row is None or row.total_count == 0 or row.present_count == 0:
        return []

    presence_rate = row.present_count / row.total_count
    if presence_rate < MIN_PRESENCE_RATE:
        logger.debug(
            "Skipping property metric %s.%s — presence rate %.0f%% below threshold",
            event_name, prop_key, presence_rate * 100,
        )
        return []

    agg_values: dict[str, float | None] = {
        "avg": float(row.avg_val) if row.avg_val is not None else None,
        "p95": float(row.p95_val) if row.p95_val is not None else None,
    }

    results: list[Metrics] = []
    for agg in aggregations:
        value = agg_values.get(agg)
        if value is None:
            continue
        metric = Metrics(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            metric_name=f"property.{event_name}.{prop_key}.{agg}",
            metric_timestamp=metric_timestamp,
            value=value,
            tags={"event_name": event_name, "property": prop_key, "aggregation": agg},
            created_at=metric_timestamp,
        )
        db.add(metric)
        results.append(metric)
        logger.debug(
            "property metric %s.%s.%s = %.4f (n=%d, presence=%.0f%%)",
            event_name, prop_key, agg, value, row.present_count, presence_rate * 100,
        )

    return results
