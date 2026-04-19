import datetime
import logging
import uuid

from sqlalchemy.orm import Session

from app.config import settings
from app.llm.insights import generate_insight, generate_trend_insight
from app.models.anomaly import Anomalies
from app.models.event import EventTypes
from app.models.insight import Insights
from app.models.trend import Trends

logger = logging.getLogger(__name__)


def _format_event_knowledge(et: EventTypes) -> str | None:
    """Format an EventTypes row's knowledge into a prompt-ready string. Returns None if empty."""
    parts = []
    if et.description:
        parts.append(f'Description: "{et.description}"')
    if et.type_metadata:
        if et.type_metadata.get("category"):
            parts.append(f'Category: {et.type_metadata["category"]}')
        if et.type_metadata.get("business_context"):
            parts.append(f'Business context: {et.type_metadata["business_context"]}')
        if et.type_metadata.get("related_events"):
            parts.append(f'Related events: {", ".join(et.type_metadata["related_events"])}')
    return "\n".join(parts) if parts else None


def generate_insights(db: Session, anomalies: list[Anomalies]) -> list[Insights]:
    """
    Generate LLM insights for each anomaly and persist them to the insights table.
    Failures on individual anomalies are logged and skipped so one bad call
    doesn't abort the whole batch.

    Returns the list of Insights rows written.
    """
    if not anomalies or not settings.llm_configured:
        if not settings.llm_configured:
            logger.warning("No LLM API key set — skipping insight generation")
        return []

    results: list[Insights] = []
    now = datetime.datetime.now(datetime.UTC)

    for anomaly in anomalies:
        try:
            metric_name = anomaly.metric_name or ""
            if metric_name.startswith("property."):
                # property.{event_name}.{prop_key}.{agg} → event_name is second segment
                parts = metric_name.split(".", 3)
                event_name = parts[1] if len(parts) >= 2 else metric_name
            else:
                event_name = metric_name.removeprefix("event_count.")
            et = (
                db.query(EventTypes)
                .filter(EventTypes.tenant_id == anomaly.tenant_id, EventTypes.event_name == event_name)
                .first()
            )
            event_knowledge = _format_event_knowledge(et) if et else None
            output = generate_insight(anomaly, event_knowledge=event_knowledge)
        except Exception:
            logger.exception("Failed to generate insight for anomaly %s", anomaly.id)
            continue

        insight = Insights(
            id=uuid.uuid4(),
            tenant_id=anomaly.tenant_id,
            anomaly_id=anomaly.id,
            title=output.title,
            summary=output.summary,
            explanation=output.explanation,
            confidence=output.confidence,
            created_at=now,
        )
        db.add(insight)
        results.append(insight)

    db.flush()
    return results


def generate_trend_insights(db: Session, trends: list[Trends]) -> list[Insights]:
    """
    Generate LLM insights for each trend and persist them. Mirrors generate_insights
    but sets trend_id instead of anomaly_id.

    Returns the list of Insights rows written.
    """
    if not trends or not settings.llm_configured:
        if not settings.llm_configured:
            logger.warning("No LLM API key set — skipping trend insight generation")
        return []

    results: list[Insights] = []
    now = datetime.datetime.now(datetime.UTC)

    for trend in trends:
        try:
            metric_name = trend.metric_name or ""
            if metric_name.startswith("property."):
                parts = metric_name.split(".", 3)
                event_name = parts[1] if len(parts) >= 2 else metric_name
            else:
                event_name = metric_name.removeprefix("event_count.")

            et = (
                db.query(EventTypes)
                .filter(EventTypes.tenant_id == trend.tenant_id, EventTypes.event_name == event_name)
                .first()
            )
            event_knowledge = _format_event_knowledge(et) if et else None
            output = generate_trend_insight(trend, event_knowledge=event_knowledge)
        except Exception:
            logger.exception("Failed to generate insight for trend %s", trend.id)
            continue

        insight = Insights(
            id=uuid.uuid4(),
            tenant_id=trend.tenant_id,
            trend_id=trend.id,
            title=output.title,
            summary=output.summary,
            explanation=output.explanation,
            confidence=output.confidence,
            created_at=now,
        )
        db.add(insight)
        results.append(insight)

    db.flush()
    return results
