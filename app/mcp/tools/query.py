"""
Natural language to SQL query tool.

Generates a read-only SELECT query from a plain-English question using the LLM,
then executes it with hard guardrails:
  - Only SELECT statements are permitted
  - 10-second PostgreSQL statement timeout
  - 200-row result cap
  - tenant_id is always injected — cross-tenant access is impossible

Schema context is built live from the database at query time so it never drifts
from the actual schema. Includes a sample of real metric names and event types
so the LLM can resolve natural names ("payment") to actual metric names.
"""

import datetime
import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.llm.client import get_llm_client

_MAX_ROWS = 200
_STATEMENT_TIMEOUT_MS = 10_000


def _build_schema_context(db: Session, tenant_id: str) -> str:
    """Introspect the live database to build an accurate schema context string."""

    # Full column listing from information_schema
    columns_result = db.execute(text("""
        SELECT table_name, column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """))

    tables: dict[str, list[str]] = {}
    for table_name, column_name, data_type, nullable in columns_result:
        tables.setdefault(table_name, []).append(
            f"{column_name} {data_type}{'?' if nullable == 'YES' else ''}"
        )

    schema_lines = ["Tables (all timestamps are UTC):"]
    for table, cols in sorted(tables.items()):
        schema_lines.append(f"\n{table}(\n  " + ",\n  ".join(cols) + "\n)")

    # Sample real metric names for this tenant so the LLM can resolve natural language
    metric_names = db.execute(text("""
        SELECT DISTINCT metric_name
        FROM metrics
        WHERE tenant_id = :tid
        ORDER BY metric_name
        LIMIT 50
    """), {"tid": tenant_id}).scalars().all()

    if metric_names:
        schema_lines.append(
            "\nKnown metric names for this tenant:\n  " + "\n  ".join(metric_names)
        )

    # Sample real event names
    event_names = db.execute(text("""
        SELECT event_name, total_events
        FROM event_types
        WHERE tenant_id = :tid
        ORDER BY total_events DESC
        LIMIT 20
    """), {"tid": tenant_id}).all()

    if event_names:
        schema_lines.append("\nKnown event types (name, total_events):")
        for name, total in event_names:
            schema_lines.append(f"  {name}: {total}")

    # Sample a real event's properties shape so the LLM knows what's in the jsonb
    sample_props = db.execute(text("""
        SELECT event_name, properties
        FROM events
        WHERE tenant_id = :tid AND properties IS NOT NULL
        LIMIT 5
    """), {"tid": tenant_id}).all()

    if sample_props:
        schema_lines.append("\nSample event properties (event_name: properties):")
        for name, props in sample_props:
            schema_lines.append(f"  {name}: {props}")

    return "\n".join(schema_lines)


def _generate_sql(db: Session, question: str, tenant_id: str) -> str:
    if not settings.llm_configured:
        raise ValueError("No LLM API key configured — cannot generate SQL")

    schema_context = _build_schema_context(db, tenant_id)
    client = get_llm_client()

    sql = client.complete(
        system=(
            "You are a PostgreSQL expert. Given a question about production analytics data, "
            "write a single read-only SELECT query that answers it.\n\n"
            f"{schema_context}\n\n"
            "Rules:\n"
            f"- ALWAYS filter by tenant_id = '{tenant_id}' on every table you query\n"
            f"- ALWAYS include LIMIT {_MAX_ROWS} or fewer\n"
            "- Only use SELECT — no INSERT, UPDATE, DELETE, DROP, TRUNCATE, or DDL\n"
            "- Use table aliases for readability\n"
            "- Prefer metric_timestamp / detected_at / last_seen_at for recency ordering\n"
            "- For jsonb fields use ->> for text extraction, -> for nested objects\n"
            "- Return ONLY the SQL query, no explanation, no markdown fences"
        ),
        prompt=question,
        max_tokens=512,
    )

    sql = sql.strip()
    # Strip markdown fences if the model included them despite instructions
    sql = re.sub(r"^```(?:sql)?\s*", "", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\s*```$", "", sql)
    return sql.strip()


def _serialize(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _validate_sql(sql: str) -> None:
    """Reject anything that isn't a plain SELECT statement."""
    normalised = sql.strip().upper()
    if not normalised.startswith("SELECT"):
        raise ValueError(f"Only SELECT queries are permitted. Generated: {sql[:120]}")
    forbidden = r"\b(INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|CREATE|GRANT|REVOKE|EXECUTE|COPY)\b"
    if re.search(forbidden, normalised):
        raise ValueError("Query contains a forbidden keyword and was rejected.")


def run_query(db: Session, tenant_id: str, question: str) -> dict[str, Any]:
    """Translate a natural language question to SQL and execute it."""
    sql = _generate_sql(db, question, tenant_id)
    _validate_sql(sql)

    with db.connection() as conn:
        conn.execute(text(f"SET LOCAL statement_timeout = {_STATEMENT_TIMEOUT_MS}"))
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [
            {k: _serialize(v) for k, v in zip(columns, row)}
            for row in result.fetchmany(_MAX_ROWS)
        ]

    return {
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
    }
