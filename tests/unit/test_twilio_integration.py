"""Unit tests for the Twilio integration module."""
import datetime
import uuid

import pytest

from app.integrations.twilio import _MAX_BODY_CHARS, format_sms_alert
from app.models.anomaly import Anomalies
from app.models.insight import Insights


def _make_insight_and_anomaly(
    severity: str = "high",
    current: float = 1.0,
    baseline: float = 12.0,
    deviation: float = -91.5,
) -> tuple[Insights, Anomalies]:
    now = datetime.datetime.now(datetime.UTC)
    tid = uuid.uuid4()
    mid = uuid.uuid4()
    anomaly = Anomalies(
        id=uuid.uuid4(), tenant_id=tid, metric_id=mid,
        metric_name="event_count.signup", metric_timestamp=now,
        current_value=current, baseline_value=baseline,
        deviation_percent=deviation, severity=severity,
        detected_at=now, context={},
    )
    insight = Insights(
        id=uuid.uuid4(), tenant_id=tid, anomaly_id=anomaly.id,
        title="Signups Dropped Over 90%",
        summary="New user signups plummeted to just 1.",
        explanation="Likely a broken signup flow.",
        confidence=0.95, created_at=now,
    )
    return insight, anomaly


@pytest.mark.unit
def test_format_includes_severity_prefix() -> None:
    insight, anomaly = _make_insight_and_anomaly(severity="critical")
    result = format_sms_alert(insight, anomaly)
    assert result.startswith("[CRITICAL]")


@pytest.mark.unit
def test_format_includes_title() -> None:
    insight, anomaly = _make_insight_and_anomaly()
    result = format_sms_alert(insight, anomaly)
    assert "Signups Dropped Over 90%" in result


@pytest.mark.unit
def test_format_includes_stats() -> None:
    insight, anomaly = _make_insight_and_anomaly(current=1.0, baseline=12.0, deviation=-91.5)
    result = format_sms_alert(insight, anomaly)
    assert "1" in result
    assert "12" in result
    assert "-91.5%" in result


@pytest.mark.unit
def test_format_spike_uses_up_arrow() -> None:
    insight, anomaly = _make_insight_and_anomaly(current=480.0, baseline=120.0, deviation=300.0)
    result = format_sms_alert(insight, anomaly)
    assert "↑" in result


@pytest.mark.unit
def test_format_drop_uses_down_arrow() -> None:
    insight, anomaly = _make_insight_and_anomaly(current=1.0, baseline=12.0, deviation=-91.5)
    result = format_sms_alert(insight, anomaly)
    assert "↓" in result


@pytest.mark.unit
def test_format_includes_summary() -> None:
    insight, anomaly = _make_insight_and_anomaly()
    result = format_sms_alert(insight, anomaly)
    assert "New user signups plummeted to just 1." in result


@pytest.mark.unit
def test_format_stays_within_twilio_limit() -> None:
    insight, anomaly = _make_insight_and_anomaly()
    result = format_sms_alert(insight, anomaly)
    assert len(result) <= _MAX_BODY_CHARS


@pytest.mark.unit
def test_format_unknown_severity_uses_alert_prefix() -> None:
    insight, anomaly = _make_insight_and_anomaly(severity="unknown")
    result = format_sms_alert(insight, anomaly)
    assert result.startswith("[ALERT]")
