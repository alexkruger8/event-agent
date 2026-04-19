"""
Integration tests for the notification service.
Mocks Slack and Twilio — tests DB persistence only.
Requires a running database (docker compose up -d).
"""
import datetime
import uuid
from unittest.mock import patch

import pytest
from slack_sdk.errors import SlackApiError
from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.metric import Metrics
from app.models.tenant import Tenants
from app.models.trend import Trends
from app.services.notification import send_slack_notifications, send_sms_notifications


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


@pytest.fixture()
def slack_tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(
        id=tid, name="slack-tenant",
        created_at=datetime.datetime.now(datetime.UTC),
        slack_channel="alerts",
    ))
    db.flush()
    return tid


@pytest.fixture()
def sms_tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(
        id=tid, name="sms-tenant",
        created_at=datetime.datetime.now(datetime.UTC),
        sms_recipients=["+15551234567", "+15559876543"],
    ))
    db.flush()
    return tid


def _make_insight_with_anomaly(db: Session, tenant_id: uuid.UUID) -> Insights:
    now = datetime.datetime.now(datetime.UTC)

    metric = Metrics(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_name="event_count.page_view",
        metric_timestamp=now, value=480.0, tags={}, created_at=now,
    )
    db.add(metric)
    db.flush()

    anomaly = Anomalies(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_id=metric.id,
        metric_name="event_count.page_view", metric_timestamp=now,
        current_value=480.0, baseline_value=120.0, deviation_percent=300.0,
        severity="critical", detected_at=now, context={},
    )
    db.add(anomaly)
    db.flush()

    insight = Insights(
        id=uuid.uuid4(), tenant_id=tenant_id, anomaly_id=anomaly.id,
        title="Page views spiked", summary="Traffic surged.", explanation="Investigate sources.",
        confidence=0.85, created_at=now,
    )
    db.add(insight)
    db.flush()

    db.refresh(insight, ["anomaly"])
    return insight


# ── Slack ────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_slack_sends_and_persists_notification(db: Session, slack_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, slack_tenant_id)

    with patch("app.services.notification.post_insight", return_value="1234567890.123456"):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.slack_bot_token = "xoxb-test"
            notifications = send_slack_notifications(db, [insight])

    assert len(notifications) == 1
    assert notifications[0].external_message_id == "1234567890.123456"
    assert notifications[0].channel == "slack"
    assert notifications[0].tenant_id == slack_tenant_id


@pytest.mark.integration
def test_slack_skips_when_no_bot_token(db: Session, slack_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, slack_tenant_id)

    with patch("app.services.notification.settings") as mock_settings:
        mock_settings.slack_bot_token = None
        notifications = send_slack_notifications(db, [insight])

    assert notifications == []


@pytest.mark.integration
def test_slack_skips_when_no_channel_configured(db: Session, tenant_id: uuid.UUID) -> None:
    """Tenant with no slack_channel set should produce no notifications."""
    insight = _make_insight_with_anomaly(db, tenant_id)

    with patch("app.services.notification.settings") as mock_settings:
        mock_settings.slack_bot_token = "xoxb-test"
        notifications = send_slack_notifications(db, [insight])

    assert notifications == []


@pytest.mark.integration
def test_slack_continues_after_failure(db: Session, slack_tenant_id: uuid.UUID) -> None:
    insight1 = _make_insight_with_anomaly(db, slack_tenant_id)
    insight2 = _make_insight_with_anomaly(db, slack_tenant_id)

    call_count = 0

    def flaky_post(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise SlackApiError("channel_not_found", {"error": "channel_not_found"})  # type: ignore[no-untyped-call]
        return "1234567890.123456"

    with patch("app.services.notification.post_insight", side_effect=flaky_post):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.slack_bot_token = "xoxb-test"
            notifications = send_slack_notifications(db, [insight1, insight2])

    assert len(notifications) == 1


@pytest.mark.integration
def test_slack_empty_insights_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    assert send_slack_notifications(db, []) == []


# ── SMS ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_sms_sends_to_all_recipients(db: Session, sms_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, sms_tenant_id)

    with patch("app.services.notification.send_alert", return_value="SM123"):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.twilio_account_sid = "AC123"
            mock_settings.twilio_auth_token = "token"
            mock_settings.twilio_from_number = "+15550000000"
            notifications = send_sms_notifications(db, [insight])

    assert len(notifications) == 2
    phone_numbers = {n.external_message_id for n in notifications}
    assert phone_numbers == {"+15551234567", "+15559876543"}
    assert all(n.channel == "sms" for n in notifications)
    assert all(n.tenant_id == sms_tenant_id for n in notifications)


@pytest.mark.integration
def test_sms_skips_tenant_with_no_recipients(db: Session, tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, tenant_id)

    with patch("app.services.notification.send_alert", return_value="SM123"):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.twilio_account_sid = "AC123"
            mock_settings.twilio_auth_token = "token"
            mock_settings.twilio_from_number = "+15550000000"
            notifications = send_sms_notifications(db, [insight])

    assert notifications == []


@pytest.mark.integration
def test_sms_skips_when_twilio_not_configured(db: Session, sms_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, sms_tenant_id)

    with patch("app.services.notification.settings") as mock_settings:
        mock_settings.twilio_account_sid = None
        mock_settings.twilio_auth_token = None
        mock_settings.twilio_from_number = None
        notifications = send_sms_notifications(db, [insight])

    assert notifications == []


@pytest.mark.integration
def test_sms_continues_after_send_failure(db: Session, sms_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_anomaly(db, sms_tenant_id)

    call_count = 0

    def flaky_send(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Twilio error")
        return "SM123"

    with patch("app.services.notification.send_alert", side_effect=flaky_send):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.twilio_account_sid = "AC123"
            mock_settings.twilio_auth_token = "token"
            mock_settings.twilio_from_number = "+15550000000"
            notifications = send_sms_notifications(db, [insight])

    assert len(notifications) == 1


@pytest.mark.integration
def test_sms_empty_insights_returns_empty(db: Session, tenant_id: uuid.UUID) -> None:
    assert send_sms_notifications(db, []) == []


# ── Trend insights ────────────────────────────────────────────────────────────

def _make_insight_with_trend(db: Session, tenant_id: uuid.UUID) -> Insights:
    now = datetime.datetime.now(datetime.UTC)

    trend = Trends(
        id=uuid.uuid4(), tenant_id=tenant_id,
        metric_name="event_count.signup", direction="down",
        slope_per_hour=-15.0, change_percent_per_hour=-12.5,
        window_start=now - datetime.timedelta(hours=6), window_end=now,
        sample_size=6, mean_value=120.0,
        detected_at=now, context={"r_squared": 0.95},
    )
    db.add(trend)
    db.flush()

    insight = Insights(
        id=uuid.uuid4(), tenant_id=tenant_id, trend_id=trend.id,
        title="Signups falling steadily", summary="Signups declining 12.5%/hr.",
        explanation="Possible funnel issue.", confidence=0.8, created_at=now,
    )
    db.add(insight)
    db.flush()

    db.refresh(insight, ["trend"])
    return insight


@pytest.mark.integration
def test_slack_sends_trend_insight(db: Session, slack_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_trend(db, slack_tenant_id)

    with patch("app.services.notification.post_trend_insight", return_value="9999.000001"):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.slack_bot_token = "xoxb-test"
            notifications = send_slack_notifications(db, [insight])

    assert len(notifications) == 1
    assert notifications[0].external_message_id == "9999.000001"
    assert notifications[0].channel == "slack"


@pytest.mark.integration
def test_slack_skips_insight_with_neither_anomaly_nor_trend(
    db: Session, slack_tenant_id: uuid.UUID
) -> None:
    now = datetime.datetime.now(datetime.UTC)
    insight = Insights(
        id=uuid.uuid4(), tenant_id=slack_tenant_id,
        title="Orphan", summary="s", explanation="e", confidence=0.5, created_at=now,
    )
    db.add(insight)
    db.flush()

    with patch("app.services.notification.post_insight") as mock_post:
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.slack_bot_token = "xoxb-test"
            notifications = send_slack_notifications(db, [insight])

    assert notifications == []
    mock_post.assert_not_called()


@pytest.mark.integration
def test_sms_sends_trend_insight(db: Session, sms_tenant_id: uuid.UUID) -> None:
    insight = _make_insight_with_trend(db, sms_tenant_id)

    with patch("app.services.notification.send_alert", return_value="SM999"):
        with patch("app.services.notification.settings") as mock_settings:
            mock_settings.twilio_account_sid = "AC123"
            mock_settings.twilio_auth_token = "token"
            mock_settings.twilio_from_number = "+15550000000"
            notifications = send_sms_notifications(db, [insight])

    assert len(notifications) == 2  # two recipients
    assert all(n.channel == "sms" for n in notifications)
