"""Customer-related tools for Plane MCP Server."""

from fastmcp import FastMCP

from plane_mcp.tools.v1.customers.base import register_customer_base_tools
from plane_mcp.tools.v1.customers.properties import register_customer_property_tools
from plane_mcp.tools.v1.customers.property_values import register_customer_property_value_tools
from plane_mcp.tools.v1.customers.requests import register_customer_request_tools
from plane_mcp.tools.v1.customers.work_items import register_customer_work_item_tools

__all__ = ["register_customer_tools"]


def register_customer_tools(mcp: FastMCP) -> None:
    """Register all customer-related tools with the MCP server."""
    register_customer_base_tools(mcp)
    register_customer_property_tools(mcp)
    register_customer_property_value_tools(mcp)
    register_customer_request_tools(mcp)
    register_customer_work_item_tools(mcp)
