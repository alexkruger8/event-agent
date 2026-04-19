"""
Metric computation worker.

For each tenant, runs the full pipeline in sequence:
  1. compute_baselines        — refresh historical averages/stddevs
  2. compute_metrics          — count events in the current window
  3. compute_property_metrics — avg/p95 for tracked numeric properties
  4. detect_anomalies         — flag metrics that deviate from baseline
  5. detect_trends            — flag sustained directional movement
  6. generate_insights        — LLM explanation for each anomaly
  7. send_slack_notifications — post insights to Slack
  8. send_sms_notifications   — send insights via SMS

Intended to be called on a schedule (e.g. every metric_window_minutes).
"""
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tenant import Tenants
from app.services.anomaly import detect_anomalies
from app.services.baseline import compute_baselines
from app.services.insight import generate_insights, generate_trend_insights
from app.services.metrics import compute_metrics
from app.services.notification import send_slack_notifications, send_sms_notifications
from app.services.property_metrics import compute_property_metrics
from app.services.trend import detect_trends

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    baselines: int
    metrics: int
    property_metrics: int
    anomalies: int
    trends: int
    insights: int


def run_for_tenant(db: Session, tenant_id: uuid.UUID) -> PipelineResult:
    """Run the full pipeline for a single tenant. Returns a summary of what was produced."""
    baselines = compute_baselines(db, tenant_id)
    logger.info("tenant=%s refreshed %d baselines", tenant_id, len(baselines))

    metrics = compute_metrics(db, tenant_id)
    logger.info("tenant=%s computed %d metrics", tenant_id, len(metrics))

    property_metrics = compute_property_metrics(db, tenant_id)
    logger.info("tenant=%s computed %d property metrics", tenant_id, len(property_metrics))

    anomalies = detect_anomalies(db, metrics + property_metrics)
    logger.info("tenant=%s detected %d anomalies", tenant_id, len(anomalies))

    trends = detect_trends(db, tenant_id)
    logger.info("tenant=%s detected %d trends", tenant_id, len(trends))

    insights = generate_insights(db, anomalies)
    trend_insights = generate_trend_insights(db, trends)
    all_insights = insights + trend_insights
    logger.info("tenant=%s generated %d insights total", tenant_id, len(all_insights))

    slack_notifications = send_slack_notifications(db, all_insights)
    logger.info("tenant=%s sent %d Slack notifications", tenant_id, len(slack_notifications))

    sms_notifications = send_sms_notifications(db, all_insights)
    logger.info("tenant=%s sent %d SMS notifications", tenant_id, len(sms_notifications))

    db.commit()

    return PipelineResult(
        baselines=len(baselines),
        metrics=len(metrics),
        property_metrics=len(property_metrics),
        anomalies=len(anomalies),
        trends=len(trends),
        insights=len(all_insights),
    )


def run(db: Session) -> None:
    tenant_ids = [row[0] for row in db.query(Tenants.id).all()]

    for tenant_id in tenant_ids:
        run_for_tenant(db, tenant_id)
