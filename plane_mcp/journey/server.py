"""Plane MCP Server implementation."""

import os

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from mcp.types import Icon

from plane_mcp.auth import PlaneHeaderAuthProvider, PlaneOAuthProvider
from plane_mcp.journey.tools import register_tools


def get_oauth_mcp(base_path: str = "/"):
    import logging

    logger = logging.getLogger(__name__)

    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")

    if redis_host and redis_port:
        try:
            redis_port_int = int(redis_port)
        except ValueError:
            raise ValueError(
                f"REDIS_PORT must be a valid integer, got '{redis_port}'. "
                "Please set REDIS_PORT to a valid port number (e.g., 6379)."
            ) from None
        logger.info("Using Redis for token storage")
        client_storage = RedisStore(host=redis_host, port=redis_port_int)
    else:
        logger.warning(
            "Using in-memory storage - tokens will be lost on restart! "
            "Set REDIS_HOST and REDIS_PORT for production."
        )
        client_storage = MemoryStore()

    client_id = os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID", "dummy_client_id")
    client_secret = os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET", "dummy_client_secret")
    if client_id == "dummy_client_id" or client_secret == "dummy_client_secret":
        logger.warning(
            "OAuth provider is using placeholder credentials. "
            "Set PLANE_OAUTH_PROVIDER_CLIENT_ID and PLANE_OAUTH_PROVIDER_CLIENT_SECRET for production."
        )

    # Initialize the MCP server
    oauth_mcp = FastMCP(
        "Plane Journey MCP Server",
        icons=[Icon(src="https://plane.so/favicon.ico", alt="Plane Journey MCP Server")],
        website_url="https://plane.so",
        auth=PlaneOAuthProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=f"{os.getenv('PLANE_OAUTH_PROVIDER_BASE_URL', 'http://localhost:8211')}{base_path}",
            plane_base_url=os.getenv("PLANE_BASE_URL", ""),
            plane_internal_base_url=os.getenv("PLANE_INTERNAL_BASE_URL", ""),
            client_storage=client_storage,
            required_scopes=["read", "write"],
            allowed_client_redirect_uris=[
                # Localhost only for http (dynamic ports from MCP clients)
                "http://localhost:*",
                "http://localhost:*/*",
                "http://127.0.0.1:*",
                "http://127.0.0.1:*/*",
                # Known MCP client custom protocol schemes
                "cursor://*",
                "vscode://*",
                "vscode-insiders://*",
                "windsurf://*",
                "claude://*",
            ],
        ),
    )
    register_tools(oauth_mcp)
    return oauth_mcp


def get_header_mcp():
    header_mcp = FastMCP(
        "Plane MCP Server (header-http)",
        auth=PlaneHeaderAuthProvider(
            required_scopes=["read", "write"],
        ),
    )
    register_tools(header_mcp)
    return header_mcp


def get_stdio_mcp():
    stdio_mcp = FastMCP(
        "Plane Journey MCP Server (stdio)",
    )
    register_tools(stdio_mcp)
    return stdio_mcp
