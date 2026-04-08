"""Journey tools initialization."""

from fastmcp import FastMCP

from .create_update import register_create_update_tools
from .read import register_read_tools
from .workflow import register_workflow_tools

def register_tools(mcp: FastMCP) -> None:
    """Register all journey tools with the MCP server."""
    register_read_tools(mcp)
    register_create_update_tools(mcp)
    register_workflow_tools(mcp)
