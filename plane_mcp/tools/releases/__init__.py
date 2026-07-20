"""Release-related tools for Plane MCP Server.

Split to mirror the SDK's `plane.api.releases` sub-resources: the releases
themselves, their version tags, workspace labels, and the labels and work
items attached to a release.

Releases are gated by a workspace feature flag; calls fail if it is off.
"""

from fastmcp import FastMCP

from plane_mcp.tools.releases.base import register_release_base_tools
from plane_mcp.tools.releases.labels import register_release_label_tools
from plane_mcp.tools.releases.tags import register_release_tag_tools
from plane_mcp.tools.releases.work_items import register_release_work_item_tools

__all__ = ["register_release_tools"]


def register_release_tools(mcp: FastMCP) -> None:
    """Register all release-related tools with the MCP server."""
    register_release_base_tools(mcp)
    register_release_tag_tools(mcp)
    register_release_label_tools(mcp)
    register_release_work_item_tools(mcp)
