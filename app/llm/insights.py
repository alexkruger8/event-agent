import json

from pydantic import BaseModel, Field

from app.llm.client import get_llm_client
from app.models.anomaly import Anomalies
from app.models.trend import Trends


class InsightOutput(BaseModel):
    title: str = Field(description="Short headline, under 10 words")
    summary: str = Field(description="One sentence summary of what happened")
    explanation: str = Field(description="2-3 sentences on likely meaning and causes")
    confidence: float = Field(description="Confidence in this insight, 0.0 to 1.0", ge=0.0, le=1.0)


def _insight_system() -> str:
    schema = InsightOutput.model_json_schema()
    return (
        "You are an expert product analyst. Respond with a JSON object that exactly matches "
        "this schema:\n\n"
        f"{json.dumps(schema, indent=2)}"
    )


def generate_insight(
    anomaly: Anomalies,
    event_knowledge: str | None = None,
) -> InsightOutput:
    """Generate a human-readable insight for an anomaly."""
    client = get_llm_client()

    direction = "spike" if (anomaly.current_value or 0) > (anomaly.baseline_value or 0) else "drop"
    deviation_str = (
        f"{anomaly.deviation_percent:.1f}%" if anomaly.deviation_percent is not None else "unknown"
    )

    knowledge_section = (
        f"\nKnown context about this event type:\n{event_knowledge}\n"
        if event_knowledge
        else ""
    )

    prompt = f"""An anomaly was detected in event metric monitoring. Generate a concise, human-readable insight.

Metric: {anomaly.metric_name}
Direction: {direction}
Current value: {anomaly.current_value}
Baseline average: {anomaly.baseline_value}
Deviation from baseline: {deviation_str}
Severity: {anomaly.severity}
Detected at: {anomaly.detected_at}
Context: {anomaly.context}{knowledge_section}
Write for a non-technical product or business stakeholder who wants to understand what happened and why it might matter. If known context about the event type is provided, use it to make the insight more specific and meaningful."""

    text = client.complete_json(
        _insight_system(),
        prompt,
        schema=InsightOutput.model_json_schema(),
        max_tokens=1024,
    )
    return InsightOutput(**json.loads(text))


def generate_trend_insight(
    trend: Trends,
    event_knowledge: str | None = None,
) -> InsightOutput:
    """Generate a human-readable insight for a trend."""
    client = get_llm_client()

    direction_word = "rising" if trend.direction == "up" else "falling"
    change_str = (
        f"{abs(trend.change_percent_per_hour):.1f}%/hr"
        if trend.change_percent_per_hour is not None
        else "unknown rate"
    )
    r2_str = (
        f"{trend.context['r_squared']:.2f}"
        if trend.context and "r_squared" in trend.context
        else "unknown"
    )

    knowledge_section = (
        f"\nKnown context about this event type:\n{event_knowledge}\n"
        if event_knowledge
        else ""
    )

    prompt = f"""A sustained trend was detected in event metric monitoring. Generate a concise, human-readable insight.

Metric: {trend.metric_name}
Direction: {direction_word}
Rate of change: {change_str}
Mean value over window: {trend.mean_value}
Data points: {trend.sample_size}
Trend fit quality (r²): {r2_str}
Window: {trend.window_start} to {trend.window_end}
Detected at: {trend.detected_at}{knowledge_section}
Write for a non-technical product or business stakeholder. Explain what a sustained {direction_word} trend in this metric likely means and what might be causing it. If known context about the event type is provided, use it to make the insight more specific."""

    text = client.complete_json(
        _insight_system(),
        prompt,
        schema=InsightOutput.model_json_schema(),
        max_tokens=1024,
    )
    return InsightOutput(**json.loads(text))
