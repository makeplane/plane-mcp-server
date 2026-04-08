"""Main entry point for the Plane MCP Server."""

import os
import sys
from contextlib import asynccontextmanager
from enum import Enum

import uvicorn
from fastmcp.utilities.logging import get_logger
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from plane_mcp.journey.server import get_header_mcp, get_oauth_mcp, get_stdio_mcp

logger = get_logger(__name__)


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
        try:
            server_mode = ServerMode(sys.argv[1])
        except ValueError:
            valid_modes = ", ".join(m.value for m in ServerMode)
            raise ValueError(f"Invalid server mode '{sys.argv[1]}'. Valid modes: {valid_modes}") from None

    if server_mode == ServerMode.STDIO:
        # Validate API_KEY and PLANE_WORKSPACE_SLUG are set
        if not os.getenv("PLANE_API_KEY"):
            raise ValueError("PLANE_API_KEY is not set")
        if not os.getenv("PLANE_WORKSPACE_SLUG"):
            raise ValueError("PLANE_WORKSPACE_SLUG is not set")

        get_stdio_mcp().run()
        return

    if server_mode == ServerMode.SSE:
        raise NotImplementedError(
            "SSE mode is not implemented in the agent server. "
            "Use 'stdio' or 'http'. SSE transport is defined in the MCP spec "
            "but not supported by this endpoint."
        )

    if server_mode == ServerMode.HTTP:
        http_mcp = get_header_mcp()
        http_app = http_mcp.http_app(transport="streamable-http")

        app = Starlette(
            routes=[
                Mount("/", app=http_app),
            ],
            lifespan=lambda app: http_app.lifespan(http_app),
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        port = int(os.getenv("FASTMCP_PORT", "8211"))
        logger.info(f"Starting HTTP server for Streamable HTTP at / on port {port}")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
        return


if __name__ == "__main__":
    main()
