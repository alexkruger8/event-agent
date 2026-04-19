"""
Integration tests for the conversation service dispatch logic.
Tests that handle_user_message routes to the correct agent based on
whether the insight is linked to an anomaly or a trend.
Requires a running database (docker compose -f docker-compose.test.yml up -d).
"""
import datetime
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.metric import Metrics
from app.models.notification import Notifications
from app.models.tenant import Tenants
from app.models.trend import Trends
from app.services.conversation import handle_user_message


@pytest.fixture()
def tenant_id(db: Session) -> uuid.UUID:
    tid = uuid.uuid4()
    db.add(Tenants(id=tid, name="test-tenant", created_at=datetime.datetime.now(datetime.UTC)))
    db.flush()
    return tid


def _make_notification_with_anomaly(db: Session, tenant_id: uuid.UUID) -> Notifications:
    now = datetime.datetime.now(datetime.UTC)
    metric = Metrics(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_name="event_count.signup",
        metric_timestamp=now, value=200.0, tags={}, created_at=now,
    )
    db.add(metric)
    db.flush()

    anomaly = Anomalies(
        id=uuid.uuid4(), tenant_id=tenant_id, metric_id=metric.id,
        metric_name="event_count.signup", metric_timestamp=now,
        current_value=200.0, baseline_value=10.0, deviation_percent=1900.0,
        severity="critical", detected_at=now, context={},
    )
    db.add(anomaly)
    db.flush()

    insight = Insights(
        id=uuid.uuid4(), tenant_id=tenant_id, anomaly_id=anomaly.id,
        title="Signup spike", summary="Signups surged.", explanation="Unusual traffic.",
        confidence=0.9, created_at=now,
    )
    db.add(insight)
    db.flush()

    notification = Notifications(
        id=uuid.uuid4(), tenant_id=tenant_id, insight_id=insight.id,
        channel="slack", external_message_id="111.222", delivered_at=now,
    )
    db.add(notification)
    db.flush()

    db.refresh(notification, ["insight"])
    db.refresh(notification.insight, ["anomaly", "trend"])
    return notification


def _make_notification_with_trend(db: Session, tenant_id: uuid.UUID) -> Notifications:
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
        title="Signups falling", summary="Steady decline detected.",
        explanation="Could be a funnel issue.", confidence=0.8, created_at=now,
    )
    db.add(insight)
    db.flush()

    notification = Notifications(
        id=uuid.uuid4(), tenant_id=tenant_id, insight_id=insight.id,
        channel="slack", external_message_id="333.444", delivered_at=now,
    )
    db.add(notification)
    db.flush()

    db.refresh(notification, ["insight"])
    db.refresh(notification.insight, ["anomaly", "trend"])
    return notification


# ── Anomaly conversations ──────────────────────────────────────────────────────

@pytest.mark.integration
def test_anomaly_conversation_calls_run_conversation(db: Session, tenant_id: uuid.UUID) -> None:
    notification = _make_notification_with_anomaly(db, tenant_id)

    with patch("app.services.conversation.run_conversation", return_value="Anomaly response") as mock_run:
        with patch("app.services.conversation.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-test"
            response = handle_user_message(db, notification, "What caused this?")

    assert response == "Anomaly response"
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["anomaly"].metric_name == "event_count.signup"


@pytest.mark.integration
def test_anomaly_conversation_persists_messages(db: Session, tenant_id: uuid.UUID) -> None:
    from app.models.conversation import Messages
    notification = _make_notification_with_anomaly(db, tenant_id)

    with patch("app.services.conversation.run_conversation", return_value="Good question"):
        with patch("app.services.conversation.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-test"
            handle_user_message(db, notification, "Is this a spike?")

    msgs = db.query(Messages).all()
    assert len(msgs) == 2
    assert msgs[0].sender == "user"
    assert msgs[0].message == "Is this a spike?"
    assert msgs[1].sender == "assistant"
    assert msgs[1].message == "Good question"


# ── Trend conversations ────────────────────────────────────────────────────────

@pytest.mark.integration
def test_trend_conversation_calls_run_trend_conversation(db: Session, tenant_id: uuid.UUID) -> None:
    notification = _make_notification_with_trend(db, tenant_id)

    with patch("app.services.conversation.run_trend_conversation", return_value="Trend response") as mock_run:
        with patch("app.services.conversation.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-test"
            response = handle_user_message(db, notification, "Why is this falling?")

    assert response == "Trend response"
    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["trend"].metric_name == "event_count.signup"
    assert call_kwargs["trend"].direction == "down"


@pytest.mark.integration
def test_trend_conversation_does_not_call_anomaly_runner(db: Session, tenant_id: uuid.UUID) -> None:
    notification = _make_notification_with_trend(db, tenant_id)

    with patch("app.services.conversation.run_conversation") as mock_anomaly:
        with patch("app.services.conversation.run_trend_conversation", return_value="Trend response"):
            with patch("app.services.conversation.settings") as mock_settings:
                mock_settings.anthropic_api_key = "test-key"
                mock_settings.anthropic_model = "claude-test"
                handle_user_message(db, notification, "Why is this falling?")

    mock_anomaly.assert_not_called()


@pytest.mark.integration
def test_trend_conversation_persists_messages(db: Session, tenant_id: uuid.UUID) -> None:
    from app.models.conversation import Messages
    notification = _make_notification_with_trend(db, tenant_id)

    with patch("app.services.conversation.run_trend_conversation", return_value="It's a downtrend"):
        with patch("app.services.conversation.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.anthropic_model = "claude-test"
            handle_user_message(db, notification, "What's happening?")

    msgs = db.query(Messages).all()
    assert len(msgs) == 2
    assert msgs[0].sender == "user"
    assert msgs[1].sender == "assistant"
    assert msgs[1].message == "It's a downtrend"


# ── Edge cases ─────────────────────────────────────────────────────────────────

@pytest.mark.integration
def test_returns_none_when_no_api_key(db: Session, tenant_id: uuid.UUID) -> None:
    notification = _make_notification_with_anomaly(db, tenant_id)

    with patch("app.services.conversation.settings") as mock_settings:
        mock_settings.anthropic_api_key = None
        mock_settings.openai_api_key = None
        mock_settings.llm_configured = False
        response = handle_user_message(db, notification, "Hello?")

    assert response is None


@pytest.mark.integration
def test_returns_none_when_insight_has_neither(db: Session, tenant_id: uuid.UUID) -> None:
    now = datetime.datetime.now(datetime.UTC)
    insight = Insights(
        id=uuid.uuid4(), tenant_id=tenant_id,
        title="Orphan", summary="s", explanation="e", confidence=0.5, created_at=now,
    )
    db.add(insight)
    db.flush()

    notification = Notifications(
        id=uuid.uuid4(), tenant_id=tenant_id, insight_id=insight.id,
        channel="slack", external_message_id="999.000", delivered_at=now,
    )
    db.add(notification)
    db.flush()

    db.refresh(notification, ["insight"])
    db.refresh(notification.insight, ["anomaly", "trend"])

    with patch("app.services.conversation.settings") as mock_settings:
        mock_settings.anthropic_api_key = "test-key"
        mock_settings.anthropic_model = "claude-test"
        response = handle_user_message(db, notification, "Hello?")

    assert response is None
