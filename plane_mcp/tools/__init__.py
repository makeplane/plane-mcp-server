"""Tools for Plane MCP Server."""

from fastmcp import FastMCP

from plane_mcp.tools.cycles import register_cycle_tools
from plane_mcp.tools.initiatives import register_initiative_tools
from plane_mcp.tools.intake import register_intake_tools
from plane_mcp.tools.modules import register_module_tools
from plane_mcp.tools.projects import register_project_tools
from plane_mcp.tools.users import register_user_tools
from plane_mcp.tools.work_item_properties import register_work_item_property_tools
from plane_mcp.tools.work_items import register_work_item_tools


def register_tools(mcp: FastMCP) -> None:
    """Register all tools with the MCP server."""
    register_project_tools(mcp)
    register_work_item_tools(mcp)
    register_cycle_tools(mcp)
    register_user_tools(mcp)
    register_module_tools(mcp)
    register_initiative_tools(mcp)
    register_intake_tools(mcp)
    register_work_item_property_tools(mcp)
