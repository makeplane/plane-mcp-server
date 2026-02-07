"""Main entry point for the Plane MCP Server."""

import os
import sys
from contextlib import asynccontextmanager
from enum import Enum

# Configure logging BEFORE importing FastMCP to prevent Rich handler setup
from plane_mcp.logging import (
    configure_logging,
    get_log_level,
    get_uvicorn_log_config,
)

configure_logging()

import logging  # noqa: E402

import uvicorn  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from plane_mcp.logging import _get_log_format  # noqa: E402
from plane_mcp.middleware import StructuredLoggingMiddleware  # noqa: E402
from plane_mcp.server import get_header_mcp, get_oauth_mcp, get_stdio_mcp  # noqa: E402

logger = logging.getLogger(__name__)


class ServerMode(Enum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "http"


@asynccontextmanager
async def combined_lifespan(oauth_app, header_app, sse_app):
    """Combine lifespans from both OAuth and Header MCP apps."""
    # Start both lifespans
    async with oauth_app.lifespan(oauth_app):
        async with header_app.lifespan(header_app):
            async with sse_app.lifespan(sse_app):
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

        get_stdio_mcp().run()
        return

    if server_mode == ServerMode.HTTP:
        oauth_mcp = get_oauth_mcp("/http")
        oauth_app = oauth_mcp.http_app()
        header_app = get_header_mcp().http_app()

        sse_mcp = get_oauth_mcp()
        sse_app = sse_mcp.http_app(transport="sse")

        oauth_well_known = oauth_mcp.auth.get_well_known_routes(mcp_path="/mcp")
        sse_well_known = sse_mcp.auth.get_well_known_routes(mcp_path="/sse")

        app = Starlette(
            routes=[
                # Well-known routes for OAuth and Header HTTP
                *oauth_well_known,
                *sse_well_known,
                # Mount both MCP servers
                Mount("/http/api-key", app=header_app),
                Mount("/http", app=oauth_app),
                Mount("/", app=sse_app),
            ],
            lifespan=lambda app: combined_lifespan(oauth_app, header_app, sse_app),
        )

        # Use structured logging middleware for JSON, uvicorn access log for Rich
        log_format = _get_log_format()
        use_json_logging = log_format == "json"

        if use_json_logging:
            app.add_middleware(StructuredLoggingMiddleware)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        logger.info("Starting HTTP server at URLs: /mcp and /header/mcp")
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8211,
            log_level=get_log_level().lower(),
            log_config=get_uvicorn_log_config(),
            access_log=not use_json_logging,  # Use uvicorn access log for Rich/text mode
        )
        return


if __name__ == "__main__":
    main()
