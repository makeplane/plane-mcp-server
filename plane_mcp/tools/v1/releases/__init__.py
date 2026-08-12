"""Release-related tools for Plane MCP Server."""

from fastmcp import FastMCP

from plane_mcp.tools.v1.releases.base import register_release_base_tools
from plane_mcp.tools.v1.releases.changelog import register_release_changelog_tools
from plane_mcp.tools.v1.releases.labels import register_release_label_tools
from plane_mcp.tools.v1.releases.tags import register_release_tag_tools
from plane_mcp.tools.v1.releases.work_items import register_release_work_item_tools

__all__ = ["register_release_tools"]


def register_release_tools(mcp: FastMCP) -> None:
    """Register all release-related tools with the MCP server."""
    register_release_base_tools(mcp)
    register_release_tag_tools(mcp)
    register_release_label_tools(mcp)
    register_release_work_item_tools(mcp)
    register_release_changelog_tools(mcp)
