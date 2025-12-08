"""Plane MCP Server implementation."""

import os

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore

from plane_mcp.auth import PlaneHeaderAuthProvider, PlaneOAuthProvider
from plane_mcp.tools import register_tools

# Initialize the MCP server
oauth_mcp = FastMCP(
    "Plane MCP Server (oauth-http)",
    auth=PlaneOAuthProvider(
        client_id=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID"),
        client_secret=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET"),
        base_url=f"{os.getenv('PLANE_OAUTH_PROVIDER_BASE_URL',)}",
        # client_storage=RedisStore(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT")),
        client_storage=MemoryStore(),
        required_scopes=["read", "write"],
    ),
)

header_mcp = FastMCP(
    "Plane MCP Server (header-http)",
    auth=PlaneHeaderAuthProvider(
        required_scopes=["read", "write"],
    ),
)

stdio_mcp = FastMCP(
    "Plane MCP Server (stdio)",
)

register_tools([oauth_mcp, header_mcp, stdio_mcp])

