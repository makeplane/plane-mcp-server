"""Plane MCP Server implementation."""

import os

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from key_value.aio.stores.redis import RedisStore
from mcp.types import Icon

from plane_mcp.auth import PlaneHeaderAuthProvider, PlaneOAuthProvider
from plane_mcp.tools import register_tools


def get_oauth_mcp(base_path: str = "/"):
    import logging

    logger = logging.getLogger(__name__)

    redis_host = os.getenv("REDIS_HOST")
    redis_port = os.getenv("REDIS_PORT")

    if redis_host and redis_port:
        logger.info("Using Redis for token storage")
        client_storage = RedisStore(host=redis_host, port=int(redis_port))
    else:
        logger.warning(
            "Using in-memory storage - tokens will be lost on restart! "
            "Set REDIS_HOST and REDIS_PORT for production."
        )
        client_storage = MemoryStore()

    # Initialize the MCP server
    oauth_mcp = FastMCP(
        "Plane MCP Server",
        icons=[Icon(src="https://plane.so/favicon.ico", alt="Plane MCP Server")],
        website_url="https://plane.so",
        auth=PlaneOAuthProvider(
            client_id=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID", ""),
            client_secret=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET", ""),
            base_url=f"{os.getenv('PLANE_OAUTH_PROVIDER_BASE_URL')}{base_path}",
            plane_base_url=os.getenv("PLANE_BASE_URL", ""),
            plane_internal_base_url=os.getenv("PLANE_INTERNAL_BASE_URL", ""),
            client_storage=client_storage,
            required_scopes=["read", "write"],
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
        "Plane MCP Server (stdio)",
    )
    register_tools(stdio_mcp)
    return stdio_mcp
