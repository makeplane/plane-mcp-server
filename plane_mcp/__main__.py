"""Main entry point for the Plane MCP Server."""

import os
import sys

import uvicorn
from starlette.middleware.cors import CORSMiddleware

from plane_mcp.server import create_app


def main() -> None:
    """Run the MCP server."""
    if len(sys.argv) > 1:
        transport = sys.argv[1]
    else:
        transport = "stdio"

    app = create_app()

    # For HTTP transports, get the Starlette app and add CORS middleware
    if transport in {"sse", "streamable-http", "http"}:
        # Get the Starlette app for the specified transport
        if transport == "sse":
            starlette_app = app.http_app(transport="sse")
        elif transport == "streamable-http":
            starlette_app = app.http_app(transport="streamable-http")
        else:
            starlette_app = app.http_app(transport="http")

        # Add CORS middleware directly to the Starlette app
        starlette_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # Allow all origins for development; restrict in production
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Run the server with uvicorn
        uvicorn.run(starlette_app, host="127.0.0.1", port=8211)
    else:
        # For stdio transport, use the standard run method

        # Validate API_KEY and PLANE_WORKSPACE_SLUG are set
        if not os.getenv("PLANE_API_KEY"):
            raise ValueError("PLANE_API_KEY is not set")
        if not os.getenv("PLANE_WORKSPACE_SLUG"):
            raise ValueError("PLANE_WORKSPACE_SLUG is not set")

        app.run(transport=transport)


if __name__ == "__main__":
    main()

