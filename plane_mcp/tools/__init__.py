"""Tools for Plane MCP Server."""

import logging
import os
from collections.abc import Callable
from types import MappingProxyType

from fastmcp import FastMCP

from plane_mcp.tools.cycles import register_cycle_tools
from plane_mcp.tools.initiatives import register_initiative_tools
from plane_mcp.tools.intake import register_intake_tools
from plane_mcp.tools.modules import register_module_tools
from plane_mcp.tools.projects import register_project_tools
from plane_mcp.tools.users import register_user_tools
from plane_mcp.tools.work_item_properties import register_work_item_property_tools
from plane_mcp.tools.work_items import register_work_item_tools

# Map of tool group names to their registration functions (immutable)
_TOOL_GROUPS: dict[str, Callable[[FastMCP], None]] = {
    "projects": register_project_tools,
    "work_items": register_work_item_tools,
    "cycles": register_cycle_tools,
    "users": register_user_tools,
    "modules": register_module_tools,
    "initiatives": register_initiative_tools,
    "intake": register_intake_tools,
    "work_item_properties": register_work_item_property_tools,
}
TOOL_GROUPS = MappingProxyType(_TOOL_GROUPS)


def register_tools(mcp: FastMCP) -> None:
    """Register tools with the MCP server based on PLANE_TOOLS configuration.

    The PLANE_TOOLS environment variable controls which tool groups are registered:
    - Not set or empty: Register all tools (default)
    - Comma-separated list: Register only specified groups
      Example: PLANE_TOOLS=projects,work_items,users
    - Exclusion mode (prefix with !): Register all except specified groups
      Example: PLANE_TOOLS=!cycles,!modules,!initiatives

    Available tool groups: projects, work_items, cycles, users, modules,
    initiatives, intake, work_item_properties
    """
    tools_config = os.getenv("PLANE_TOOLS", "").strip()

    if not tools_config:
        # Default: register all tools
        enabled_groups = set(TOOL_GROUPS.keys())
    elif tools_config.startswith("!"):
        # Exclusion mode: start with all, remove specified groups
        # Only items prefixed with ! are excluded
        items = [t.strip() for t in tools_config.split(",")]
        excluded = {t[1:] for t in items if t.startswith("!")}
        unprefixed = {t for t in items if t and not t.startswith("!")}
        if unprefixed:
            unprefixed_str = ", ".join(sorted(unprefixed))
            suggested = ", ".join("!" + t for t in sorted(unprefixed))
            logging.warning(
                f"Unprefixed items in exclusion mode will be ignored: {unprefixed_str}. "
                f"Did you mean: {suggested}?"
            )
        # Validate excluded group names
        invalid = excluded - set(TOOL_GROUPS.keys())
        if invalid:
            logging.warning(
                f"Unknown tool groups in PLANE_TOOLS will be ignored: {', '.join(sorted(invalid))}"
            )
        enabled_groups = set(TOOL_GROUPS.keys()) - excluded
    else:
        # Inclusion mode: only register specified groups
        enabled_groups = {t.strip() for t in tools_config.split(",")}
        # Validate included group names
        invalid = enabled_groups - set(TOOL_GROUPS.keys()) - {""}
        if invalid:
            logging.warning(
                f"Unknown tool groups in PLANE_TOOLS will be ignored: {', '.join(sorted(invalid))}"
            )

    for name, register_fn in TOOL_GROUPS.items():
        if name in enabled_groups:
            register_fn(mcp)
