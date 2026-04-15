"""Main entry point for the Plane MCP Server."""

import json
import logging
import os
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import datetime, timezone
from enum import Enum

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from plane_mcp.server import get_header_mcp, get_oauth_mcp, get_stdio_mcp


class JSONFormatter(logging.Formatter):
    """JSON log formatter for structured logging (Datadog, ELK, etc.)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["error"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
            }
        return json.dumps(log_entry)


def configure_json_logging():
    """Replace FastMCP's Rich handlers with a JSON formatter on the fastmcp logger."""
    fastmcp_logger = logging.getLogger("fastmcp")

    # Remove all existing handlers (Rich)
    for handler in fastmcp_logger.handlers[:]:
        fastmcp_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    fastmcp_logger.addHandler(handler)
    fastmcp_logger.setLevel(logging.INFO)
    fastmcp_logger.propagate = False


configure_json_logging()

logger = logging.getLogger("fastmcp.plane_mcp")


class ServerMode(Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


@asynccontextmanager
async def combined_lifespan(apps):
    """Combine lifespans from multiple MCP apps."""
    async with AsyncExitStack() as stack:
        for app in apps:
            await stack.enter_async_context(app.lifespan(app))
        yield


def main() -> None:
    """Run the MCP server."""
    server_mode = ServerMode.STDIO
    if len(sys.argv) > 1:
        server_mode = ServerMode(sys.argv[1])

    if server_mode == ServerMode.STDIO:
        # Validate API_KEY and PLANE_WORKSPACE_SLUG are set
        if not os.getenv("PLANE_API_KEY"):
            raise ValueError("PLANE_API_KEY is not set")
        if not os.getenv("PLANE_WORKSPACE_SLUG"):
            raise ValueError("PLANE_WORKSPACE_SLUG is not set")

        from plane_mcp.journey.tools import register_tools as register_journey_tools
        stdio_mcp = get_stdio_mcp()
        register_journey_tools(stdio_mcp)
        stdio_mcp.run()
        return

    if server_mode == ServerMode.HTTP:
        oauth_mcp = get_oauth_mcp("/http")
        oauth_app = oauth_mcp.http_app(stateless_http=True)
        header_app = get_header_mcp().http_app(stateless_http=True)

        sse_mcp = get_oauth_mcp()
        sse_app = sse_mcp.http_app(transport="sse")

        oauth_well_known = oauth_mcp.auth.get_well_known_routes(mcp_path="/mcp")
        sse_well_known = sse_mcp.auth.get_well_known_routes(mcp_path="/sse")

        # --- AGENT JOURNEY API ---
        from plane_mcp.journey.server import (
            get_header_mcp as journey_get_header_mcp,
            get_oauth_mcp as journey_get_oauth_mcp
        )
        journey_oauth_mcp = journey_get_oauth_mcp("/agent")
        journey_oauth_app = journey_oauth_mcp.http_app(stateless_http=True)
        
        journey_header_mcp = journey_get_header_mcp()
        journey_header_app = journey_header_mcp.http_app(stateless_http=True)
        
        journey_oauth_well_known = []
        if hasattr(journey_oauth_mcp, 'auth') and journey_oauth_mcp.auth:
            journey_oauth_well_known = journey_oauth_mcp.auth.get_well_known_routes(mcp_path="/agent/mcp")

        app = Starlette(
            routes=[
                # Well-known routes for OAuth and Header HTTP
                *oauth_well_known,
                *sse_well_known,
                *journey_oauth_well_known,
                # Mount both MCP servers
                Mount("/http/api-key", app=header_app),
                Mount("/http", app=oauth_app),
                Mount("/agent/api-key", app=journey_header_app),
                Mount("/agent", app=journey_oauth_app),
                Mount("/", app=sse_app),
            ],
            lifespan=lambda app: combined_lifespan([oauth_app, header_app, sse_app, journey_oauth_app, journey_header_app]),
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Configure uvicorn loggers to use JSON formatting too
        for uv_logger_name in ("uvicorn", "uvicorn.error"):
            uv_logger = logging.getLogger(uv_logger_name)
            for h in uv_logger.handlers[:]:
                uv_logger.removeHandler(h)
            uv_handler = logging.StreamHandler(sys.stderr)
            uv_handler.setFormatter(JSONFormatter())
            uv_logger.addHandler(uv_handler)

        logger.info("Starting HTTP server at URLs: /mcp and /header/mcp")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8211,
            log_level="info",
            access_log=False,
        )
        return


if __name__ == "__main__":
    main()
