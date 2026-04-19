import datetime
import logging
import uuid

from slack_sdk.errors import SlackApiError
from sqlalchemy.orm import Session

from app.config import settings
from app.integrations.slack import post_insight, post_trend_insight
from app.integrations.twilio import format_sms_alert, format_trend_sms_alert, send_alert
from app.models.insight import Insights
from app.models.notification import Notifications
from app.models.tenant import Tenants

logger = logging.getLogger(__name__)


def send_slack_notifications(db: Session, insights: list[Insights]) -> list[Notifications]:
    """
    Send a Slack notification for each insight and record it in the notifications table.
    Skips silently if Slack is not configured. Failures on individual messages are
    logged and skipped so one bad send doesn't abort the batch.

    Returns the list of Notifications rows written.
    """
    if not insights:
        return []

    if not settings.slack_bot_token:
        logger.warning("Slack bot token not configured — skipping notifications")
        return []

    now = datetime.datetime.now(datetime.UTC)
    results: list[Notifications] = []

    for insight in insights:
        tenant = db.query(Tenants).filter(Tenants.id == insight.tenant_id).first()
        if not tenant or not tenant.slack_channel:
            logger.debug("Tenant %s has no Slack channel configured — skipping", insight.tenant_id)
            continue

        try:
            if insight.anomaly_id is not None and insight.anomaly is not None:
                ts = post_insight(
                    insight=insight,
                    anomaly=insight.anomaly,
                    token=settings.slack_bot_token,
                    channel=tenant.slack_channel,
                )
            elif insight.trend_id is not None and insight.trend is not None:
                ts = post_trend_insight(
                    insight=insight,
                    trend=insight.trend,
                    token=settings.slack_bot_token,
                    channel=tenant.slack_channel,
                )
            else:
                logger.warning("Insight %s has neither anomaly nor trend loaded — skipping", insight.id)
                continue
        except SlackApiError:
            logger.exception("Failed to send Slack notification for insight %s", insight.id)
            continue

        notification = Notifications(
            id=uuid.uuid4(),
            tenant_id=insight.tenant_id,
            insight_id=insight.id,
            channel="slack",
            external_message_id=ts,
            delivered_at=now,
        )
        db.add(notification)
        results.append(notification)
        logger.info("Sent Slack notification for insight %s (ts=%s)", insight.id, ts)

    db.flush()
    return results


def send_sms_notifications(db: Session, insights: list[Insights]) -> list[Notifications]:
    """
    Send an SMS notification to each tenant recipient for each insight.
    Skips silently if Twilio is not configured or the tenant has no recipients.
    One Notifications row is created per recipient per insight so inbound replies
    can be routed back by phone number.

    Returns the list of Notifications rows written.
    """
    if not insights:
        return []

    if not (settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number):
        logger.warning("Twilio not configured — skipping SMS notifications")
        return []

    now = datetime.datetime.now(datetime.UTC)
    results: list[Notifications] = []

    for insight in insights:
        tenant = db.query(Tenants).filter(Tenants.id == insight.tenant_id).first()
        if not tenant or not tenant.sms_recipients:
            logger.debug("Tenant %s has no SMS recipients — skipping", insight.tenant_id)
            continue

        if insight.anomaly_id is not None and insight.anomaly is not None:
            body = format_sms_alert(insight, insight.anomaly)
        elif insight.trend_id is not None and insight.trend is not None:
            body = format_trend_sms_alert(insight, insight.trend)
        else:
            logger.warning("Insight %s has neither anomaly nor trend loaded — skipping", insight.id)
            continue

        for phone_number in tenant.sms_recipients:
            try:
                sid = send_alert(
                    to_number=phone_number,
                    from_number=settings.twilio_from_number,
                    body=body,
                    account_sid=settings.twilio_account_sid,
                    auth_token=settings.twilio_auth_token,
                )
            except Exception:
                logger.exception("Failed to send SMS to %s for insight %s", phone_number, insight.id)
                continue

            notification = Notifications(
                id=uuid.uuid4(),
                tenant_id=insight.tenant_id,
                insight_id=insight.id,
                channel="sms",
                external_message_id=phone_number,
                delivered_at=now,
            )
            db.add(notification)
            results.append(notification)
            logger.info("Sent SMS to %s for insight %s (sid=%s)", phone_number, insight.id, sid)

    db.flush()
    return results
