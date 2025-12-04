"""User-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.users import UserLite

from plane_mcp.client import get_plane_client


def register_user_tools(mcp: FastMCP) -> None:
    """Register all user-related tools with the MCP server."""

    @mcp.tool()
    def get_me() -> UserLite:
        """
        Get current user information.
        
        Returns:
            UserLite object containing current user information
        """
        client = get_plane_client()
        return client.users.get_me()

