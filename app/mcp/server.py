"""
MCP server definition for AI Event Intelligence.

Exposes read-only production intelligence tools: anomalies, errors, insights,
metrics, and system health summaries.

MCP_READ_ONLY = True — enforced by construction: all tool handlers execute
SELECT-only queries. Never add write operations to MCP tools without a security review.
"""

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import mcp.types as types
from app.config import settings
from app.database.session import _get_session_local
from app.mcp.tools import anomalies as anomaly_tools
from app.mcp.tools import errors as error_tools
from app.mcp.tools import health as health_tools
from app.mcp.tools import insights as insight_tools
from app.mcp.tools import metrics as metric_tools
from app.mcp.tools import query as query_tools
from mcp.server import Server

_executor = ThreadPoolExecutor(max_workers=4)


def _resolve_tenant(tenant_id: str | None) -> str:
    """Resolve tenant_id, falling back to MCP_DEFAULT_TENANT_ID if set."""
    if tenant_id:
        return tenant_id
    if settings.mcp_default_tenant_id:
        return settings.mcp_default_tenant_id
    raise ValueError(
        "tenant_id is required (or set MCP_DEFAULT_TENANT_ID environment variable)"
    )


def _get_mcp_session_local() -> Any:
    """Return a session factory connected via MCP_DATABASE_URL (read-only user) if configured,
    otherwise fall back to the shared app session factory."""
    if settings.mcp_database_url:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(settings.mcp_database_url, pool_pre_ping=True)
        return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    return _get_session_local()


async def _run_in_db(fn: Callable[..., Any], *args: Any) -> Any:
    """Run a synchronous DB function in a thread pool to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()

    def _call() -> Any:
        SessionLocal = _get_mcp_session_local()
        db = SessionLocal()
        try:
            return fn(db, *args)
        finally:
            db.close()

    return await loop.run_in_executor(_executor, _call)


def create_server() -> Server:
    server: Server = Server("ai-event-intelligence")

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="get_system_health_summary",
                description=(
                    "Get a bird's-eye view of production health: open anomalies by severity, "
                    "unresolved errors by service, active trends, and the latest insight title. "
                    "Start here for a quick 'what's burning?' overview."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {
                            "type": "string",
                            "description": "Tenant UUID. Omit to use MCP_DEFAULT_TENANT_ID.",
                        },
                    },
                },
            ),
            types.Tool(
                name="get_recent_anomalies",
                description=(
                    "List recent unresolved statistical anomalies in event/metric patterns, "
                    "with optional severity filter. Each result includes the LLM insight summary."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["low", "medium", "high", "critical"],
                            "description": "Filter by severity level.",
                        },
                        "hours": {
                            "type": "integer",
                            "default": 24,
                            "description": "Look back this many hours (default 24).",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 20,
                            "description": "Max results to return (default 20).",
                        },
                    },
                },
            ),
            types.Tool(
                name="get_anomaly_detail",
                description=(
                    "Get full detail for a single anomaly including its latest LLM insight "
                    "explanation, raw values, and context."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "anomaly_id": {"type": "string", "description": "UUID of the anomaly."},
                    },
                    "required": ["anomaly_id"],
                },
            ),
            types.Tool(
                name="get_recent_errors",
                description=(
                    "List recent unresolved application errors, optionally filtered by service "
                    "and severity. Includes occurrence counts for frequency context."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "service": {
                            "type": "string",
                            "description": "Filter by service name (e.g. 'payment-service').",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["debug", "info", "warning", "error", "critical"],
                        },
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            ),
            types.Tool(
                name="get_unresolved_errors",
                description=(
                    "List unresolved errors sorted by occurrence count (noisiest first). "
                    "Use this to find the most impactful errors to fix."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "service": {"type": "string"},
                        "min_occurrences": {
                            "type": "integer",
                            "default": 1,
                            "description": "Only return errors seen at least this many times.",
                        },
                    },
                },
            ),
            types.Tool(
                name="get_recent_insights",
                description=(
                    "List recent LLM-generated insights with titles, summaries, and confidence scores."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "limit": {"type": "integer", "default": 10},
                    },
                },
            ),
            types.Tool(
                name="run_query",
                description=(
                    "Answer any question about production data by generating and executing a "
                    "read-only SQL query. Use this for questions the other tools can't answer — "
                    "e.g. 'how do checkout amounts correlate with page views?', "
                    "'which events fired most in the last hour?', "
                    "'show me the top 10 services by error count this week'. "
                    "Returns the generated SQL alongside the results."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "question": {
                            "type": "string",
                            "description": "Plain English question about your production data.",
                        },
                    },
                    "required": ["question"],
                },
            ),
            types.Tool(
                name="search_metric_names",
                description=(
                    "Search available metric names by keyword. Use this before get_metric_summary "
                    "when you don't know the exact metric name — e.g. search 'payment' or 'checkout' "
                    "to discover what metrics are actually tracked."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "keyword": {
                            "type": "string",
                            "description": "Partial metric name to search for (case-insensitive).",
                        },
                    },
                    "required": ["keyword"],
                },
            ),
            types.Tool(
                name="get_metric_summary",
                description=(
                    "Get a statistical summary (min/max/avg/latest) for a specific metric "
                    "over a recent time window, plus any active anomaly on that metric. "
                    "Use search_metric_names first if you're unsure of the exact name."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tenant_id": {"type": "string"},
                        "metric_name": {
                            "type": "string",
                            "description": "Exact metric name to summarize.",
                        },
                        "hours": {"type": "integer", "default": 6},
                    },
                    "required": ["metric_name"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        import json

        try:
            result = await _dispatch(name, arguments)
            return [types.TextContent(type="text", text=json.dumps(result, indent=2))]
        except ValueError as exc:
            return [types.TextContent(type="text", text=f"Error: {exc}")]

    return server


async def _dispatch(name: str, args: dict[str, Any]) -> Any:
    tenant_id = _resolve_tenant(args.get("tenant_id"))
    max_results = settings.mcp_max_results

    if name == "get_system_health_summary":
        return await _run_in_db(health_tools.get_system_health_summary, tenant_id)

    if name == "get_recent_anomalies":
        return await _run_in_db(
            anomaly_tools.get_recent_anomalies,
            tenant_id,
            args.get("severity"),
            int(args.get("hours", 24)),
            min(int(args.get("limit", 20)), max_results),
        )

    if name == "get_anomaly_detail":
        result = await _run_in_db(
            anomaly_tools.get_anomaly_detail,
            tenant_id,
            args["anomaly_id"],
        )
        if result is None:
            raise ValueError(f"Anomaly {args['anomaly_id']} not found")
        return result

    if name == "get_recent_errors":
        return await _run_in_db(
            error_tools.get_recent_errors,
            tenant_id,
            args.get("service"),
            args.get("severity"),
            min(int(args.get("limit", 20)), max_results),
        )

    if name == "get_unresolved_errors":
        return await _run_in_db(
            error_tools.get_unresolved_errors,
            tenant_id,
            args.get("service"),
            int(args.get("min_occurrences", 1)),
        )

    if name == "get_recent_insights":
        return await _run_in_db(
            insight_tools.get_recent_insights,
            tenant_id,
            min(int(args.get("limit", 10)), max_results),
        )

    if name == "search_metric_names":
        return await _run_in_db(
            metric_tools.search_metric_names,
            tenant_id,
            args["keyword"],
        )

    if name == "get_metric_summary":
        return await _run_in_db(
            metric_tools.get_metric_summary,
            tenant_id,
            args["metric_name"],
            int(args.get("hours", 6)),
        )

    if name == "run_query":
        return await _run_in_db(query_tools.run_query, tenant_id, args["question"])

    raise ValueError(f"Unknown tool: {name}")
