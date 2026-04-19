"""
General-purpose conversational agent for the web chat UI.

Unlike run_conversation/run_trend_conversation (which are anchored to a specific
anomaly/insight), this agent opens with a snapshot of the full tenant state and
lets the user ask anything about their event data.
"""
import datetime
import logging
import re
import uuid
from collections.abc import Generator
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
- metrics(id UUID, tenant_id UUID, metric_name TEXT, metric_timestamp TIMESTAMP, value DOUBLE PRECISION, tags JSONB, created_at TIMESTAMP)
- metric_baselines(id UUID, tenant_id UUID, metric_name TEXT, avg_value DOUBLE PRECISION, stddev DOUBLE PRECISION, sample_size INT, computed_at TIMESTAMP)
- anomalies(id UUID, tenant_id UUID, metric_name TEXT, current_value DOUBLE PRECISION, baseline_value DOUBLE PRECISION, deviation_percent DOUBLE PRECISION, severity TEXT, detected_at TIMESTAMP, resolved_at TIMESTAMP, context JSONB)
- trends(id UUID, tenant_id UUID, metric_name TEXT, direction TEXT, change_percent_per_hour DOUBLE PRECISION, mean_value DOUBLE PRECISION, window_start TIMESTAMP, window_end TIMESTAMP, sample_size INT, resolved_at TIMESTAMP, context JSONB)
- insights(id UUID, tenant_id UUID, anomaly_id UUID, trend_id UUID, title TEXT, summary TEXT, explanation TEXT, confidence DOUBLE PRECISION, created_at TIMESTAMP)
- errors(id UUID, tenant_id UUID, error_type TEXT, message TEXT, service TEXT, severity TEXT, fingerprint TEXT, occurrence_count INT, first_seen_at TIMESTAMP, last_seen_at TIMESTAMP, resolved_at TIMESTAMP)

metric_name patterns:
- "event_count.<event_name>" — event volume per hour
- "property.<event_name>.<property_key>.<aggregation>" — numeric property metrics
""".strip()

_SQL_TOOL = {
    "name": "execute_sql",
    "description": (
        "Execute a read-only SQL SELECT query against the events database. "
        "Use this to answer questions about event volumes, metrics, anomalies, errors, trends, "
        "or anything else in the data. Only SELECT statements are permitted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "A valid PostgreSQL SELECT statement."}
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

_UPDATE_KNOWLEDGE_TOOL = {
    "name": "update_event_type_knowledge",
    "description": (
        "Save or update what you've learned about an event type from the user — "
        "its meaning, business context, related events, or category."
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
        "Use action='add' to start tracking, action='remove' to stop."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "properties": {"type": "array", "items": {"type": "string"}},
            "action": {"type": "string", "enum": ["add", "remove"]},
        },
        "required": ["event_name", "properties", "action"],
        "additionalProperties": False,
    },
}

_EXPLORE_PROPERTIES_TOOL = {
    "name": "explore_event_properties",
    "description": (
        "Scan recent events for an event type and return which property keys appear most often "
        "and which are consistently numeric. Use to discover trackable properties."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "event_name": {"type": "string"},
            "sample_size": {"type": "integer"},
        },
        "required": ["event_name"],
        "additionalProperties": False,
    },
}

_NUMERIC_RE = r"^-?[0-9]+\.?[0-9]*$"


def _execute_sql(query: str, db: Session) -> str:
    stripped = query.strip().upper()
    if not stripped.startswith("SELECT"):
        return "Error: only SELECT queries are permitted."
    if re.search(r";\s*\w", query):
        return "Error: multi-statement queries are not permitted."
    savepoint = db.begin_nested()
    try:
        result = db.execute(text(query))
        rows = result.fetchmany(50)
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


def _update_event_type_knowledge(
    event_name: str,
    description: str | None,
    metadata: dict | None,  # type: ignore[type-arg]
    db: Session,
    tenant_id: uuid.UUID,
) -> str:
    et = db.query(EventTypes).filter(
        EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name
    ).first()
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
    return f"Saved knowledge for '{event_name}': {', '.join(parts) if parts else 'no changes'}."


def _update_tracked_properties(
    event_name: str, properties: list[str], action: str, db: Session, tenant_id: uuid.UUID
) -> str:
    et = db.query(EventTypes).filter(
        EventTypes.tenant_id == tenant_id, EventTypes.event_name == event_name
    ).first()
    if et is None:
        return f"Error: event type '{event_name}' not found."
    existing_meta: dict = et.type_metadata or {}  # type: ignore[type-arg]
    tracked: dict[str, list[str]] = existing_meta.get("tracked_properties") or {}
    if action == "add":
        for p in properties:
            if p not in tracked:
                tracked[p] = ["avg", "p95"]
        verb, metric_names = "Now tracking", [f"property.{event_name}.{p}.avg/.p95" for p in properties]
    else:
        for p in properties:
            tracked.pop(p, None)
        verb, metric_names = "Stopped tracking", [f"property.{event_name}.{p}" for p in properties]
    et.type_metadata = {**existing_meta, "tracked_properties": tracked}
    db.flush()
    return f"{verb} for '{event_name}': {', '.join(metric_names)}."


def _explore_event_properties(
    event_name: str, sample_size: int, db: Session, tenant_id: uuid.UUID
) -> str:
    row = db.execute(
        text("""
            WITH sampled AS (
                SELECT properties FROM events
                WHERE tenant_id = :tid AND event_name = :name
                ORDER BY timestamp DESC LIMIT :n
            ),
            total AS (SELECT COUNT(*) AS n FROM sampled),
            keys AS (
                SELECT key,
                       COUNT(*) AS present_count,
                       COUNT(*) FILTER (WHERE value ~ :re) AS numeric_count
                FROM sampled, jsonb_each_text(properties)
                GROUP BY key
            )
            SELECT total.n AS total_events,
                   jsonb_agg(jsonb_build_object(
                       'key', keys.key,
                       'present_count', keys.present_count,
                       'numeric_count', keys.numeric_count
                   ) ORDER BY keys.present_count DESC) AS props
            FROM keys, total GROUP BY total.n
        """),
        {"tid": str(tenant_id), "name": event_name, "n": sample_size, "re": _NUMERIC_RE},
    ).first()
    if row is None or not row.props:
        return f"No events found for '{event_name}'."
    total = row.total_events
    lines = [f"Property scan for '{event_name}' ({total} events sampled):"]
    numeric_candidates: list[str] = []
    for prop in row.props:
        pct = prop["present_count"] / total * 100
        npct = prop["numeric_count"] / prop["present_count"] * 100 if prop["present_count"] else 0
        flag = " ★ numeric" if npct >= 90 else (" ~ partly numeric" if npct >= 50 else "")
        if npct >= 90:
            numeric_candidates.append(prop["key"])
        lines.append(f"  {prop['key']}: {pct:.0f}% present, {npct:.0f}% numeric{flag}")
    if numeric_candidates:
        lines.append(f"\nSuggested to track: {', '.join(numeric_candidates)}")
    return "\n".join(lines)


def _build_tenant_context(db: Session, tenant_id: uuid.UUID) -> str:
    """Snapshot of the tenant's current state to seed the system prompt."""
    lines: list[str] = []

    # Open anomalies
    anomalies = (
        db.query(Anomalies)
        .filter(Anomalies.tenant_id == tenant_id, Anomalies.resolved_at.is_(None))
        .order_by(Anomalies.detected_at.desc())
        .limit(10)
        .all()
    )
    if anomalies:
        lines.append("Open anomalies:")
        for a in anomalies:
            lines.append(
                f"  - {a.metric_name} | {a.severity} | {a.deviation_percent:+.1f}% from baseline"
                f" | detected {a.detected_at.strftime('%b %d %H:%M') if a.detected_at else '?'}"
            )
    else:
        lines.append("No open anomalies.")

    # Active trends
    trends = (
        db.query(Trends)
        .filter(Trends.tenant_id == tenant_id, Trends.resolved_at.is_(None))
        .order_by(Trends.detected_at.desc())
        .limit(5)
        .all()
    )
    if trends:
        lines.append("\nActive trends:")
        for t in trends:
            direction = "↑" if t.direction == "up" else "↓"
            rate = f"{t.change_percent_per_hour:+.1f}%/hr" if t.change_percent_per_hour is not None else "?"
            lines.append(f"  - {t.metric_name} {direction} {rate}")

    # Recent insights
    insights = (
        db.query(Insights)
        .filter(Insights.tenant_id == tenant_id)
        .order_by(Insights.created_at.desc())
        .limit(5)
        .all()
    )
    if insights:
        lines.append("\nRecent insights:")
        for ins in insights:
            lines.append(f"  - {ins.title}: {ins.summary}")

    # Event type knowledge
    event_types = (
        db.query(EventTypes)
        .filter(EventTypes.tenant_id == tenant_id)
        .order_by(EventTypes.event_name)
        .all()
    )
    if event_types:
        lines.append("\nKnown event types:")
        for et in event_types:
            desc = f'"{et.description}"' if et.description else "(no description)"
            lines.append(f"  - {et.event_name}: {desc}")

    return "\n".join(lines)


def _tool_status(name: str, tool_input: dict[str, Any]) -> str:
    """Human-readable status line shown while a tool is executing."""
    if name == "execute_sql":
        return "Querying your event data..."
    if name == "explore_event_properties":
        return f"Scanning properties for **{tool_input.get('event_name', '...')}**..."
    if name == "update_event_type_knowledge":
        return f"Saving knowledge about **{tool_input.get('event_name', '...')}**..."
    if name == "update_tracked_properties":
        verb = "Adding" if tool_input.get("action") == "add" else "Removing"
        return f"{verb} tracked metrics for **{tool_input.get('event_name', '...')}**..."
    return "Working..."


def stream_general_conversation(
    user_message: str,
    history: list[dict[str, Any]],
    tenant_id: uuid.UUID,
    db: Session,
) -> Generator[dict[str, Any], None, None]:
    """
    Generator that streams the agent loop as events:
      {"type": "status", "text": str}  — shown in the thinking indicator
      {"type": "done",   "text": str}  — final markdown response
      {"type": "error",  "text": str}  — terminal error

    history: list of {"role": "user"|"assistant", "content": str} dicts from prior turns.
    """
    client = get_llm_client()
    tenant_context = _build_tenant_context(db, tenant_id)
    now_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    system = f"""{_SCHEMA_CONTEXT}

You are an AI analyst embedded in a real-time event intelligence platform. \
The user is a developer or engineer who wants to understand what's happening \
in their system's event stream.

Current time: {now_str}
Tenant ID (always filter queries by this): {tenant_id}

Current system state:
{tenant_context}

You have four capabilities:
1. ANALYST: Run SQL queries with execute_sql to answer any question about events, \
metrics, anomalies, errors, or trends. Always scope queries with \
WHERE tenant_id = '{tenant_id}'.
2. LEARNER: When the user explains what an event means, save it with \
update_event_type_knowledge. Confirm what you saved.
3. TRACKER: When the user wants to track a numeric property, use \
update_tracked_properties. First explore with explore_event_properties if unsure \
what's available.
4. GUIDE: Proactively surface the most important thing happening — open anomalies, \
active trends, recent errors — if the user opens with a vague question like \
"what's going on?" or "anything unusual?".

Be concise. Use markdown formatting. Explain query results in plain English."""

    messages: list[dict[str, Any]] = [
        {"role": turn["role"], "content": turn["content"]} for turn in history
    ]
    messages.append({"role": "user", "content": user_message})

    tools = [_SQL_TOOL, _UPDATE_KNOWLEDGE_TOOL, _UPDATE_TRACKED_PROPERTIES_TOOL, _EXPLORE_PROPERTIES_TOOL]

    yield {"type": "status", "text": "Analyzing your event stream..."}

    while True:
        response = client.call_with_tools(system, messages, tools)
        client.append_assistant(messages, response)

        text, tool_calls = client.parse_response(response)

        if client.is_done(response):
            yield {"type": "done", "text": text or ""}
            return

        results: list[tuple[str, str]] = []
        for tc in tool_calls:
            yield {"type": "status", "text": _tool_status(tc.name, tc.input)}

            if tc.name == "execute_sql":
                logger.info("Chat SQL: %s", tc.input.get("query", "")[:120])
                result = _execute_sql(tc.input["query"], db)
            elif tc.name == "update_event_type_knowledge":
                result = _update_event_type_knowledge(
                    tc.input["event_name"],
                    tc.input.get("description"),
                    tc.input.get("metadata"),
                    db,
                    tenant_id,
                )
            elif tc.name == "update_tracked_properties":
                result = _update_tracked_properties(
                    tc.input["event_name"],
                    tc.input["properties"],
                    tc.input["action"],
                    db,
                    tenant_id,
                )
            elif tc.name == "explore_event_properties":
                result = _explore_event_properties(
                    tc.input["event_name"],
                    int(tc.input.get("sample_size") or 200),
                    db,
                    tenant_id,
                )
            else:
                result = f"Unknown tool '{tc.name}'."
            results.append((tc.id, result))

        client.append_tool_results(messages, results)


def run_general_conversation(
    user_message: str,
    history: list[dict[str, Any]],
    tenant_id: uuid.UUID,
    db: Session,
) -> str:
    """Blocking wrapper around stream_general_conversation. Returns the final text."""
    for event in stream_general_conversation(user_message, history, tenant_id, db):
        if event["type"] == "done":
            return str(event["text"])
    return ""
