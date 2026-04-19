"""
Twilio SMS integration.

Handles outbound alert messages and conversational replies.
"""
from twilio.rest import Client

from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.trend import Trends

_SEVERITY_PREFIX = {
    "low": "[LOW]",
    "medium": "[MEDIUM]",
    "high": "[HIGH]",
    "critical": "[CRITICAL]",
}

# Conservative limit: Twilio allows 1600 chars for ASCII but only 1530 for unicode.
# LLM responses regularly contain smart quotes, em dashes etc, so we use 1500.
_MAX_BODY_CHARS = 1500


def format_sms_alert(insight: Insights, anomaly: Anomalies) -> str:
    """Format an insight as a concise SMS alert."""
    severity = _SEVERITY_PREFIX.get(anomaly.severity or "", "[ALERT]")
    direction = "↑" if (anomaly.current_value or 0) > (anomaly.baseline_value or 0) else "↓"
    deviation_str = (
        f"{anomaly.deviation_percent:+.1f}%" if anomaly.deviation_percent is not None else "unknown"
    )
    stats = (
        f"{anomaly.current_value:,.0f} vs baseline {anomaly.baseline_value:,.0f}"
        f" ({direction} {deviation_str})"
    )
    return f"{severity} {insight.title}\n{stats}\n{insight.summary}\nReply to investigate."


def format_trend_sms_alert(insight: Insights, trend: Trends) -> str:
    """Format a trend insight as a concise SMS alert."""
    direction_arrow = "↑" if trend.direction == "up" else "↓"
    change_str = (
        f"{abs(trend.change_percent_per_hour):.1f}%/hr"
        if trend.change_percent_per_hour is not None
        else "unknown rate"
    )
    return f"[TREND] {insight.title}\n{trend.metric_name} {direction_arrow} {change_str}\n{insight.summary}\nReply to investigate."


def send_alert(
    to_number: str,
    from_number: str,
    body: str,
    account_sid: str,
    auth_token: str,
) -> str:
    """Send an SMS alert. Returns the Twilio message SID."""
    client = Client(account_sid, auth_token)
    message = client.messages.create(to=to_number, from_=from_number, body=body)
    return str(message.sid)


def send_reply(
    to_number: str,
    from_number: str,
    text: str,
    account_sid: str,
    auth_token: str,
) -> None:
    """Send an SMS reply, truncating to Twilio's body limit if necessary."""
    client = Client(account_sid, auth_token)
    client.messages.create(
        to=to_number,
        from_=from_number,
        body=text[:_MAX_BODY_CHARS],
    )
