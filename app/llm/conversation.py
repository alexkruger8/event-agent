"""
Conversational agent with SQL and knowledge-update tools.

Given a user message and conversation history, runs an agentic loop where
Claude can execute read-only SQL queries against the events database to
answer questions, and can persist semantic knowledge about event types
learned from the user.
"""
import datetime
import logging
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.llm.client import get_llm_client
from app.models.anomaly import Anomalies
from app.models.event import EventTypes
from app.models.insight import Insights
from app.models.trend import Trends

logger = logging.getLogger(__name__)

_SCHEMA_CONTEXT = """
You have access to a PostgreSQL database with the following tables:

- tenants(id UUID, name TEXT, created_at TIMESTAMP)
- events(id UUID, tenant_id UUID, event_name TEXT, user_id TEXT, timestamp TIMESTAMP, properties JSONB, ingested_at TIMESTAMP)
- event_types(id UUID, tenant_id UUID, event_name TEXT, first_seen TIMESTAMP, last_seen TIMESTAMP, total_events BIGINT, description TEXT, metadata JSONB)
- metrics(id UUID, tenant_id UUID, metric_name TEXT, metric_timestamp TIMESTAMP, value DOUBLE, tags JSONB, created_at TIMESTAMP)
- metric_baselines(id UUID, tenant_id UUID, metric_name TEXT, avg_value DOUBLE, stddev DOUBLE, sample_size INT, computed_at TIMESTAMP)
- anomalies(id UUID, tenant_id UUID, metric_id UUID, metric_name TEXT, metric_timestamp TIMESTAMP, current_value DOUBLE, baseline_value DOUBLE, deviation_percent DOUBLE, severity TEXT, detected_at TIMESTAMP, context JSONB)
- insights(id UUID, tenant_id UUID, anomaly_id UUID, title TEXT, summary TEXT, explanation TEXT, confidence DOUBLE, created_at TIMESTAMP)
- notifications(id UUID, tenant_id UUID, insight_id UUID, channel TEXT, external_message_id TEXT, delivered_at TIMESTAMP)

metric_name values follow two patterns:
- "event_count.<event_name>" (e.g. "event_count.page_view") — event volume
- "property.<event_name>.<property_key>.<aggregation>" (e.g. "property.checkout.amount.avg") — numeric property metrics
""".strip()

_SQL_TOOL = {
    "name": "execute_sql",
    "description": (
        "Execute a read-only SQL SELECT query against the events database. "
        "Use this to look up event counts, metrics, anomaly history, trends, or any other data "
        "needed to answer the user's question. Only SELECT statements are permitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A valid PostgreSQL SELECT statement.",
            }
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}


_UPDATE_KNOWLEDGE_TOOL = {
    "name": "update_event_type_knowledge",
    "description": (
        "Save or update what you've learned about an event type from the user. "
        "Call this whenever the user explains what an event means, its business context, "
        "or how it relates to other events."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "description": {"type": "string"},
            "metadata": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "related_events": {"type": "array", "items": {"type": "string"}},
                    "business_context": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "required": ["event_name"],
        "additionalProperties": False,
    },
}


_UPDATE_TRACKED_PROPERTIES_TOOL = {
    "name": "update_tracked_properties",
    "description": (
        "Add or remove numeric event properties to track for metric computation. "
        "When a user says 'track checkout amounts' or 'stop tracking signup plan_tier', "
        "call this to configure which properties get avg/p95 metrics computed automatically. "
        "Use action='add' to start tracking, action='remove' to stop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "properties": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of property keys to add or remove from tracking.",
            },
            "action": {
                "type": "string",
                "enum": ["add", "remove"],
                "description": "Whether to start or stop tracking these properties.",
            },
        },
        "required": ["event_name", "properties", "action"],
        "additionalProperties": False,
    },
}


_EXPLORE_PROPERTIES_TOOL = {
    "name": "explore_event_properties",
    "description": (
        "Scan recent events for an event type and return which property keys appear most often "
        "and which of those are consistently numeric. Use this to discover trackable properties "
        "before suggesting update_tracked_properties to the user. Call it when the user asks "
        "what properties are available, or when you want to proactively suggest numeric properties "
        "worth tracking on an event type that has none configured yet."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "sample_size": {
                "type": "integer",
                "description": "Number of recent events to sample. Defaults to 200.",
            },
        },
        "required": ["event_name"],
        "additionalProperties": False,
    },
}


_UPDATE_ANOMALY_STATUS_TOOL = {
    "name": "update_anomaly_status",
    "description": (
        "Mark the current anomaly as acknowledged or resolved. "
        "Call this when the user says they are aware of and investigating the issue (acknowledge), "
        "or that the issue is fixed, explained, or was a false positive (resolve). "
        "Resolving suppresses further alerts for this metric until a new anomaly is detected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["acknowledged", "resolved"],
                "description": (
                    "acknowledged: user is aware and investigating. "
                    "resolved: issue is fixed or was a false positive."
                ),
            },
        },
        "required": ["status"],
        "additionalProperties": False,
    },
}


_UPDATE_TREND_STATUS_TOOL = {
    "name": "update_trend_status",
    "description": (
        "Mark the current trend as resolved. "
        "Call this when the user says the trend has been explained, addressed, or was a false positive. "
        "Resolving suppresses further alerts for this metric trend until a new one is detected."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": ["resolved"],
                "description": "resolved: trend has been addressed or was a false positive.",
            },
        },
        "required": ["status"],
        "additionalProperties": False,
    },
}


def _update_trend_status(status: str, trend: Trends, db: Session) -> str:
    """Set resolved_at on the current trend."""
    if status == "resolved":
        trend.resolved_at = datetime.datetime.now(datetime.UTC)
        db.flush()
        return (
            f"Marked trend for '{trend.metric_name}' as resolved. "
            "Further trend alerts for this metric will be suppressed until a new trend is detected."
        )
    return f"Unknown status '{status}'."


def _update_anomaly_status(status: str, anomaly: Anomalies, db: Session) -> str:
    """Update acknowledged_at or resolved_at on the current anomaly."""
    now = datetime.datetime.now(datetime.UTC)
    if status == "acknowledged":
        anomaly.acknowledged_at = now
        db.flush()
        return f"Marked anomaly for '{anomaly.metric_name}' as acknowledged."
    if status == "resolved":
        anomaly.resolved_at = now
        db.flush()
        return (
            f"Marked anomaly for '{anomaly.metric_name}' as resolved. "
            "Further alerts for this metric will be suppressed until a new anomaly is detected."
        )
    return f"Unknown status '{status}'."


_NUMERIC_RE = r"^-?[0-9]+\.?[0-9]*$"


def _explore_event_properties(
    event_name: str,
    sample_size: int,
    db: Session,
    tenant_id: uuid.UUID,
) -> str:
    """
    Sample recent events and report property key frequencies + numeric rates.
    Returns a formatted summary the agent can relay to the user.
    """
    row = db.execute(
        text("""
            WITH sampled AS (
                SELECT properties
                FROM events
                WHERE tenant_id = :tenant_id
                  AND event_name = :event_name
                ORDER BY timestamp DESC
                LIMIT :sample_size
            ),
            total AS (SELECT COUNT(*) AS n FROM sampled),
            keys AS (
                SELECT key,
                       COUNT(*) AS present_count,
                       COUNT(*) FILTER (
                           WHERE value ~ :numeric_re
                       ) AS numeric_count
                FROM sampled, jsonb_each_text(properties)
                GROUP BY key
            )
            SELECT
                total.n AS total_events,
                jsonb_agg(
                    jsonb_build_object(
                        'key',           keys.key,
                        'present_count', keys.present_count,
                        'numeric_count', keys.numeric_count
                    )
                    ORDER BY keys.present_count DESC
                ) AS props
            FROM keys, total
            GROUP BY total.n
        """),
        {
            "tenant_id": str(tenant_id),
            "event_name": event_name,
            "sample_size": sample_size,
            "numeric_re": _NUMERIC_RE,
        },
    ).first()

    if row is None or not row.props:
        return (
            f"No events found for '{event_name}' in this tenant. "
            "Either the event type doesn't exist or no events have been ingested yet."
        )

    total = row.total_events
    lines = [f"Property scan for '{event_name}' (sampled {total} recent events):\n"]
    numeric_candidates: list[str] = []

    for prop in row.props:
        key = prop["key"]
        presence_pct = prop["present_count"] / total * 100
        numeric_pct = prop["numeric_count"] / prop["present_count"] * 100 if prop["present_count"] else 0
        flag = ""
        if numeric_pct >= 90:
            flag = " ★ numeric"
            numeric_candidates.append(key)
        elif numeric_pct >= 50:
            flag = " ~ partly numeric"
        lines.append(
            f"  {key}: present on {presence_pct:.0f}% of events, "
            f"numeric {numeric_pct:.0f}%{flag}"
        )

    if numeric_candidates:
        lines.append(
            f"\nSuggested properties to track: {', '.join(numeric_candidates)}"
            "\n(These will generate avg and p95 metrics each pipeline run.)"
        )
    else:
        lines.append("\nNo consistently numeric properties found in this sample.")

    return "\n".join(lines)


def _update_tracked_properties(
    event_name: str,
    properties: list[str],
    action: str,
    db: Session,
    tenant_id: uuid.UUID,
) -> str:
    """Add or remove properties from tracked_properties in event_types.metadata."""
    et = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name)
        .first()
    )
    if et is None:
        return f"Error: event type '{event_name}' not found for this tenant."

    existing_meta: dict = et.type_metadata or {}  # type: ignore[type-arg]
    tracked: dict[str, list[str]] = existing_meta.get("tracked_properties") or {}

    if action == "add":
        for prop in properties:
            if prop not in tracked:
                tracked[prop] = ["avg", "p95"]
        verb = "Now tracking"
        metric_names = [
            f"property.{event_name}.{p}.avg / .p95" for p in properties
        ]
    else:  # remove
        for prop in properties:
            tracked.pop(prop, None)
        verb = "Stopped tracking"
        metric_names = [f"property.{event_name}.{p}" for p in properties]

    et.type_metadata = {**existing_meta, "tracked_properties": tracked}
    db.flush()

    names_str = ", ".join(metric_names)
    return f"{verb} properties for '{event_name}': {names_str}."


def _knowledge_gap_prompt(event_name: str, db: Session, tenant_id: uuid.UUID) -> str:
    """
    Return a directive to ask the user about an undescribed event type, or an empty string
    if the event type already has a description.
    Only inject this on the first conversation turn.
    """
    et = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name)
        .first()
    )
    if et is not None and et.description:
        return ""
    return (
        f"\nKNOWLEDGE GAP: The event type '{event_name}' at the center of this anomaly has no "
        f"description yet. In your first response, after briefly addressing the user's question, "
        f"ask them what '{event_name}' means in their system — what it represents, what triggers it, "
        f"and why it matters to the business."
    )


def _load_event_type_knowledge(db: Session, tenant_id: uuid.UUID) -> str:
    """Return a formatted snapshot of all known event types for this tenant."""
    rows = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id)
        .order_by(EventTypes.event_name)
        .all()
    )
    if not rows:
        return "No event types recorded for this tenant yet."

    lines = ["Known event types:"]
    for et in rows:
        meta_parts = []
        if et.type_metadata:
            if et.type_metadata.get("category"):
                meta_parts.append(f"category: {et.type_metadata['category']}")
            if et.type_metadata.get("related_events"):
                meta_parts.append(f"related: {', '.join(et.type_metadata['related_events'])}")
            if et.type_metadata.get("business_context"):
                meta_parts.append(f"context: {et.type_metadata['business_context']}")

        desc = f'"{et.description}"' if et.description else "(no description yet)"
        meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""
        lines.append(f"- {et.event_name}: {desc}{meta_str}")

    return "\n".join(lines)


def _update_event_type_knowledge(
    event_name: str,
    description: str | None,
    metadata: dict | None,  # type: ignore[type-arg]
    db: Session,
    tenant_id: uuid.UUID,
) -> str:
    """Persist learned knowledge about an event type. Returns a confirmation or error string."""
    et = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name)
        .first()
    )
    if et is None:
        return f"Error: event type '{event_name}' not found for this tenant."

    if description is not None:
        et.description = description

    if metadata is not None:
        existing = et.type_metadata or {}
        et.type_metadata = {**existing, **metadata}

    db.flush()

    parts = []
    if description is not None:
        parts.append(f'description: "{description}"')
    if metadata is not None:
        parts.append(f"metadata: {metadata}")
    saved = ", ".join(parts) if parts else "no changes"
    return f"Saved knowledge for '{event_name}': {saved}."


def _execute_sql(query: str, db: Session, tenant_id: str) -> str:
    """Execute a SELECT query safely and return results as a formatted string."""
    stripped = query.strip().upper()
    if not stripped.startswith("SELECT"):
        return "Error: only SELECT queries are permitted."

    # Block any attempt to escape the SELECT via semicolons or comments
    if re.search(r";\s*\w", query):
        return "Error: multi-statement queries are not permitted."

    savepoint = db.begin_nested()
    try:
        result = db.execute(text(query))
        rows = result.fetchmany(50)  # cap at 50 rows
        savepoint.commit()
        if not rows:
            return "Query returned no rows."

        cols = list(result.keys())
        header = " | ".join(cols)
        divider = "-" * len(header)
        lines = [header, divider] + [" | ".join(str(v) for v in row) for row in rows]
        return "\n".join(lines)
    except Exception as e:
        savepoint.rollback()
        return f"Query error: {e}"


def run_conversation(
    user_message: str,
    history: list[dict[str, Any]],
    insight: Insights,
    anomaly: Anomalies,
    db: Session,
) -> str:
    """
    Run the agentic loop for a single user turn. Returns the final text response.

    history: list of {"role": "user"|"assistant", "content": str} dicts from prior turns.
    """
    client = get_llm_client()

    knowledge_snapshot = _load_event_type_knowledge(db, anomaly.tenant_id)  # type: ignore[arg-type]

    primary_event_name = (anomaly.metric_name or "").removeprefix("event_count.")
    gap_prompt = (
        _knowledge_gap_prompt(primary_event_name, db, anomaly.tenant_id)  # type: ignore[arg-type]
        if not history
        else ""
    )

    system = f"""{_SCHEMA_CONTEXT}

You are an AI analyst helping a user investigate an anomaly that was detected in their event data.

Anomaly context:
- Metric: {anomaly.metric_name}
- Current value: {anomaly.current_value}
- Baseline average: {anomaly.baseline_value}
- Deviation: {anomaly.deviation_percent:.1f}% from baseline
- Severity: {anomaly.severity}
- Detected at: {anomaly.detected_at}

Insight generated:
- Title: {insight.title}
- Summary: {insight.summary}
- Explanation: {insight.explanation}

Current knowledge about this tenant's event types:
{knowledge_snapshot}

You serve four roles simultaneously:
1. ANALYST: Answer questions by querying the database with execute_sql.
2. LEARNER: When the user explains what an event means or how their system works, extract
   that knowledge and call update_event_type_knowledge to persist it. Confirm to the user
   what you saved.
3. TRACKER: When the user asks to track a numeric property (e.g. "track checkout amounts",
   "monitor plan_tier on signups"), call update_tracked_properties to configure it. Confirm
   the metric names that will be generated (e.g. property.checkout.amount.avg/.p95).
   When they ask to stop tracking, use action="remove".
   If an event type has no tracked_properties configured yet, proactively call
   explore_event_properties on the first turn to discover numeric candidates and suggest
   them to the user. Also call it when the user asks "what properties are available" or
   "what can I track".
4. RESOLVER: When the user indicates they are aware of and investigating the issue, call
   update_anomaly_status with "acknowledged". When they say the issue is fixed, explained,
   or was a false positive, call update_anomaly_status with "resolved". Resolving suppresses
   further alerts for this metric.{gap_prompt}

Always filter queries by tenant_id = '{anomaly.tenant_id}' to scope results to their data.
Be concise and explain query results in plain English. If a query returns no useful data, say so."""

    tools = [
        _SQL_TOOL, _EXPLORE_PROPERTIES_TOOL, _UPDATE_KNOWLEDGE_TOOL,
        _UPDATE_TRACKED_PROPERTIES_TOOL, _UPDATE_ANOMALY_STATUS_TOOL,
    ]
    messages: list[dict[str, Any]] = [
        {"role": turn["role"], "content": turn["content"]} for turn in history
    ]
    messages.append({"role": "user", "content": user_message})

    response: Any = None
    while True:
        response = client.call_with_tools(system, messages, tools)
        client.append_assistant(messages, response)

        if client.is_done(response):
            break

        _, tool_calls = client.parse_response(response)
        results: list[tuple[str, str]] = []
        for tc in tool_calls:
            tool_input = tc.input
            if tc.name == "explore_event_properties":
                logger.info("Exploring properties for: %s", tool_input.get("event_name", ""))
                result = _explore_event_properties(
                    event_name=tool_input["event_name"],
                    sample_size=int(tool_input.get("sample_size") or 200),
                    db=db,
                    tenant_id=anomaly.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "execute_sql":
                logger.info("Executing SQL: %s", tool_input.get("query", ""))
                result = _execute_sql(tool_input["query"], db, str(anomaly.tenant_id))
            elif tc.name == "update_event_type_knowledge":
                logger.info("Updating event type knowledge: %s", tool_input.get("event_name", ""))
                result = _update_event_type_knowledge(
                    event_name=tool_input["event_name"],
                    description=tool_input.get("description"),
                    metadata=tool_input.get("metadata"),
                    db=db,
                    tenant_id=anomaly.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "update_tracked_properties":
                logger.info(
                    "Updating tracked properties for %s: %s",
                    tool_input.get("event_name", ""),
                    tool_input.get("action", ""),
                )
                result = _update_tracked_properties(
                    event_name=tool_input["event_name"],
                    properties=tool_input["properties"],
                    action=tool_input["action"],
                    db=db,
                    tenant_id=anomaly.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "update_anomaly_status":
                logger.info("Updating anomaly status: %s", tool_input.get("status", ""))
                result = _update_anomaly_status(tool_input.get("status", ""), anomaly, db)
            else:
                result = f"Error: unknown tool '{tc.name}'."
            results.append((tc.id, result))

        client.append_tool_results(messages, results)

    text, _ = client.parse_response(response)
    return text or ""


def run_trend_conversation(
    user_message: str,
    history: list[dict[str, Any]],
    insight: Insights,
    trend: Trends,
    db: Session,
) -> str:
    """
    Run the agentic loop for a trend conversation. Returns the final text response.

    Mirrors run_conversation but uses trend context in the system prompt and exposes
    update_trend_status instead of update_anomaly_status.
    """
    client = get_llm_client()

    knowledge_snapshot = _load_event_type_knowledge(db, trend.tenant_id)  # type: ignore[arg-type]

    direction_word = "rising" if trend.direction == "up" else "falling"
    change_str = (
        f"{abs(trend.change_percent_per_hour):.1f}%/hr"
        if trend.change_percent_per_hour is not None
        else "unknown"
    )
    r2_str = (
        f"{trend.context['r_squared']:.2f}"
        if trend.context and "r_squared" in trend.context
        else "unknown"
    )

    primary_event_name = (trend.metric_name or "").removeprefix("event_count.")
    if (trend.metric_name or "").startswith("property."):
        parts = (trend.metric_name or "").split(".", 3)
        primary_event_name = parts[1] if len(parts) >= 2 else primary_event_name

    gap_prompt = (
        _knowledge_gap_prompt(primary_event_name, db, trend.tenant_id)  # type: ignore[arg-type]
        if not history
        else ""
    )

    system = f"""{_SCHEMA_CONTEXT}

You are an AI analyst helping a user investigate a sustained trend detected in their event data.

Trend context:
- Metric: {trend.metric_name}
- Direction: {direction_word}
- Rate of change: {change_str}
- Mean value over window: {trend.mean_value}
- Window: {trend.window_start} to {trend.window_end}
- Data points: {trend.sample_size}
- Trend fit quality (r²): {r2_str}

Insight generated:
- Title: {insight.title}
- Summary: {insight.summary}
- Explanation: {insight.explanation}

Current knowledge about this tenant's event types:
{knowledge_snapshot}

You serve four roles simultaneously:
1. ANALYST: Answer questions by querying the database with execute_sql.
2. LEARNER: When the user explains what an event means or how their system works, extract
   that knowledge and call update_event_type_knowledge to persist it. Confirm to the user
   what you saved.
3. TRACKER: When the user asks to track a numeric property (e.g. "track checkout amounts"),
   call update_tracked_properties to configure it. Confirm the metric names that will be
   generated. When they ask to stop tracking, use action="remove".
   Call explore_event_properties when the user asks what properties are available, or when
   you want to proactively suggest numeric properties worth tracking.
4. RESOLVER: When the user says the trend has been explained, addressed, or was a false
   positive, call update_trend_status with "resolved". This suppresses further trend alerts
   for this metric until a new trend is detected.{gap_prompt}

Always filter queries by tenant_id = '{trend.tenant_id}' to scope results to their data.
Be concise and explain query results in plain English. If a query returns no useful data, say so."""

    tools = [
        _SQL_TOOL, _EXPLORE_PROPERTIES_TOOL, _UPDATE_KNOWLEDGE_TOOL,
        _UPDATE_TRACKED_PROPERTIES_TOOL, _UPDATE_TREND_STATUS_TOOL,
    ]
    messages: list[dict[str, Any]] = [
        {"role": turn["role"], "content": turn["content"]} for turn in history
    ]
    messages.append({"role": "user", "content": user_message})

    response: Any = None
    while True:
        response = client.call_with_tools(system, messages, tools)
        client.append_assistant(messages, response)

        if client.is_done(response):
            break

        _, tool_calls = client.parse_response(response)
        results: list[tuple[str, str]] = []
        for tc in tool_calls:
            tool_input = tc.input
            if tc.name == "explore_event_properties":
                logger.info("Exploring properties for: %s", tool_input.get("event_name", ""))
                result = _explore_event_properties(
                    event_name=tool_input["event_name"],
                    sample_size=int(tool_input.get("sample_size") or 200),
                    db=db,
                    tenant_id=trend.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "execute_sql":
                logger.info("Executing SQL: %s", tool_input.get("query", ""))
                result = _execute_sql(tool_input["query"], db, str(trend.tenant_id))
            elif tc.name == "update_event_type_knowledge":
                logger.info("Updating event type knowledge: %s", tool_input.get("event_name", ""))
                result = _update_event_type_knowledge(
                    event_name=tool_input["event_name"],
                    description=tool_input.get("description"),
                    metadata=tool_input.get("metadata"),
                    db=db,
                    tenant_id=trend.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "update_tracked_properties":
                logger.info(
                    "Updating tracked properties for %s: %s",
                    tool_input.get("event_name", ""),
                    tool_input.get("action", ""),
                )
                result = _update_tracked_properties(
                    event_name=tool_input["event_name"],
                    properties=tool_input["properties"],
                    action=tool_input["action"],
                    db=db,
                    tenant_id=trend.tenant_id,  # type: ignore[arg-type]
                )
            elif tc.name == "update_trend_status":
                logger.info("Updating trend status: %s", tool_input.get("status", ""))
                result = _update_trend_status(tool_input.get("status", ""), trend, db)
            else:
                result = f"Error: unknown tool '{tc.name}'."
            results.append((tc.id, result))

        client.append_tool_results(messages, results)

    text, _ = client.parse_response(response)
    return text or ""
