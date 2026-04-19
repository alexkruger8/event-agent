"""
Unit tests for the Slack integration layer.
Mocks the Slack WebClient so no real messages are sent.
"""
import datetime
import uuid
from unittest.mock import patch

import pytest

from app.integrations.slack import post_insight
from app.models.anomaly import Anomalies
from app.models.insight import Insights


def _make_insight() -> Insights:
    i = Insights()
    i.id = uuid.uuid4()
    i.tenant_id = uuid.uuid4()
    i.title = "Page views spiked 4x above normal"
    i.summary = "Page view count hit 480, far above the baseline of 120."
    i.explanation = "This spike likely indicates a traffic surge. Investigate referral sources."
    i.confidence = 0.85
    i.created_at = datetime.datetime.now(datetime.UTC)
    return i


def _make_anomaly() -> Anomalies:
    a = Anomalies()
    a.id = uuid.uuid4()
    a.metric_name = "event_count.page_view"
    a.current_value = 480.0
    a.baseline_value = 120.0
    a.deviation_percent = 300.0
    a.severity = "critical"
    a.detected_at = datetime.datetime.now(datetime.UTC)
    return a


@pytest.mark.unit
def test_post_insight_returns_ts() -> None:
    mock_response = {"ts": "1234567890.123456", "ok": True}

    with patch("app.integrations.slack.WebClient") as mock_client_cls:
        mock_client_cls.return_value.chat_postMessage.return_value = mock_response
        ts = post_insight(_make_insight(), _make_anomaly(), token="xoxb-test", channel="#alerts")

    assert ts == "1234567890.123456"


@pytest.mark.unit
def test_post_insight_sends_to_correct_channel() -> None:
    mock_response = {"ts": "1234567890.123456", "ok": True}

    with patch("app.integrations.slack.WebClient") as mock_client_cls:
        mock_instance = mock_client_cls.return_value
        mock_instance.chat_postMessage.return_value = mock_response
        post_insight(_make_insight(), _make_anomaly(), token="xoxb-test", channel="#anomaly-alerts")

        call_kwargs = mock_instance.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#anomaly-alerts"
        assert "blocks" in call_kwargs


@pytest.mark.unit
def test_message_blocks_include_metric_name() -> None:
    from app.integrations.slack import _format_message
    blocks = _format_message(_make_insight(), _make_anomaly())
    block_text = str(blocks)
    assert "event_count.page_view" in block_text
    assert "480" in block_text
