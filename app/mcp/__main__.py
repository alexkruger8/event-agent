"""
MCP server entry point — stdio transport for Claude Code integration.

Usage (add to .mcp.json in project root):
    python -m app.mcp

Environment variables required:
    DATABASE_URL  — PostgreSQL connection string

Optional:
    MCP_API_KEY            — if set, must match settings.mcp_api_key
    MCP_DEFAULT_TENANT_ID  — convenience: tools use this tenant when tenant_id is omitted
"""

import asyncio
import sys

from app.config import settings
from app.mcp.server import create_server
from mcp.server.stdio import stdio_server


async def main() -> None:
    if not settings.database_url:
        print(
            "ERROR: DATABASE_URL environment variable is not set. "
            "The MCP server cannot connect to the database.",
            file=sys.stderr,
        )
        sys.exit(1)

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


asyncio.run(main())
