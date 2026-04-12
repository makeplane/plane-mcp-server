"""Work item activity-related tools for Plane MCP Server."""

from fastmcp import FastMCP


def register_work_item_activity_tools(mcp: FastMCP) -> None:
    """
    Register work item activity-related tools on the given MCP server.
    
    Currently no specific tools are registered here because list and retrieve operations for work item activities are handled by the unified `entity_list` and `entity_retrieve` tools.
    """
    # List and retrieve operations moved to entity_list / entity_retrieve unified tools.
