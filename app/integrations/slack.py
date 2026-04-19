from typing import Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError  # noqa: F401 — re-exported for callers

from app.models.anomaly import Anomalies
from app.models.insight import Insights
from app.models.trend import Trends

_SEVERITY_EMOJI = {
    "low": ":large_yellow_circle:",
    "medium": ":large_orange_circle:",
    "high": ":red_circle:",
    "critical": ":rotating_light:",
}


def _format_message(insight: Insights, anomaly: Anomalies) -> list[dict[str, Any]]:
    emoji = _SEVERITY_EMOJI.get(anomaly.severity or "", ":large_yellow_circle:")
    direction = "↑" if (anomaly.current_value or 0) > (anomaly.baseline_value or 0) else "↓"
    deviation_str = (
        f"{anomaly.deviation_percent:+.1f}%" if anomaly.deviation_percent is not None else "unknown"
    )

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {insight.title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": insight.summary},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Metric*\n{anomaly.metric_name}"},
                {"type": "mrkdwn", "text": f"*Severity*\n{(anomaly.severity or '').capitalize()}"},
                {"type": "mrkdwn", "text": f"*Current value*\n{anomaly.current_value:,.0f}"},
                {"type": "mrkdwn", "text": f"*Baseline*\n{anomaly.baseline_value:,.0f}"},
                {"type": "mrkdwn", "text": f"*Deviation*\n{direction} {deviation_str}"},
                {"type": "mrkdwn", "text": f"*Confidence*\n{(insight.confidence or 0):.0%}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*What this means*\n{insight.explanation}"},
        },
        {"type": "divider"},
    ]


def _format_trend_message(insight: Insights, trend: Trends) -> list[dict[str, Any]]:
    direction_arrow = "↑" if trend.direction == "up" else "↓"
    direction_word = "Rising" if trend.direction == "up" else "Falling"
    change_str = (
        f"{abs(trend.change_percent_per_hour):.1f}%/hr"
        if trend.change_percent_per_hour is not None
        else "unknown"
    )

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f":chart_with_{'upwards' if trend.direction == 'up' else 'downwards'}_trend: {insight.title}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": insight.summary},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Metric*\n{trend.metric_name}"},
                {"type": "mrkdwn", "text": f"*Direction*\n{direction_arrow} {direction_word}"},
                {"type": "mrkdwn", "text": f"*Rate*\n{change_str}"},
                {"type": "mrkdwn", "text": f"*Samples*\n{trend.sample_size}"},
                {"type": "mrkdwn", "text": f"*Confidence*\n{(insight.confidence or 0):.0%}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*What this means*\n{insight.explanation}"},
        },
        {"type": "divider"},
    ]


def post_trend_insight(
    insight: Insights,
    trend: Trends,
    token: str,
    channel: str,
) -> str:
    """Post a trend insight to Slack. Returns the Slack message timestamp."""
    client = WebClient(token=token)
    blocks = _format_trend_message(insight, trend)
    response = client.chat_postMessage(channel=channel, blocks=blocks)
    return str(response["ts"])


def post_insight(
    insight: Insights,
    anomaly: Anomalies,
    token: str,
    channel: str,
) -> str:
    """
    Post an insight to Slack. Returns the Slack message timestamp (ts),
    which serves as the external_message_id for deduplication.

    Raises SlackApiError on failure.
    """
    client = WebClient(token=token)
    blocks = _format_message(insight, anomaly)

    response = client.chat_postMessage(channel=channel, blocks=blocks)
    return str(response["ts"])


def post_reply(text: str, channel: str, thread_ts: str, token: str) -> None:
    """Post a plain-text reply into an existing Slack thread."""
    client = WebClient(token=token)
    client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
