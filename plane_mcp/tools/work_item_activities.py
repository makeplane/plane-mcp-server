"""Work item activity-related tools for Plane MCP Server."""

from fastmcp import FastMCP


def register_work_item_activity_tools(mcp: FastMCP) -> None:
    """Register all work item activity-related tools with the MCP server."""
    # List and retrieve operations moved to entity_list / entity_retrieve unified tools.
