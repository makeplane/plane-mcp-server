"""Plane MCP Server implementation."""

import os

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore

from plane_mcp.plane_oauth_provider import PlaneOAuthProvider
from plane_mcp.tools import (
    register_cycle_tools,
    register_initiative_tools,
    register_module_tools,
    register_project_tools,
    register_user_tools,
    register_work_item_property_tools,
    register_work_item_tools,
)

# Initialize the MCP server
mcp = FastMCP(
    "Plane MCP Server",
    auth=PlaneOAuthProvider(
        client_id=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_ID"),
        client_secret=os.getenv("PLANE_OAUTH_PROVIDER_CLIENT_SECRET"),
        base_url=os.getenv("PLANE_OAUTH_PROVIDER_BASE_URL"),
        # client_storage=RedisStore(host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT")),
        client_storage=MemoryStore(),
        required_scopes=["read", "write"],
    ),
)

# Register all tools
register_project_tools(mcp)
register_work_item_tools(mcp)
register_cycle_tools(mcp)
register_user_tools(mcp)
register_module_tools(mcp)
register_initiative_tools(mcp)
register_work_item_property_tools(mcp)




def create_app() -> FastMCP:
    """
    Create and configure the FastMCP application.

    Returns:
        Configured FastMCP instance
    """
    return mcp
