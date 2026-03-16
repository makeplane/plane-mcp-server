"""Tools for Plane MCP Server."""

import logging
import os

from fastmcp import FastMCP

from plane_mcp.tools.cycles import register_cycle_tools
from plane_mcp.tools.epics import register_epic_tools
from plane_mcp.tools.initiatives import register_initiative_tools
from plane_mcp.tools.intake import register_intake_tools
from plane_mcp.tools.labels import register_label_tools
from plane_mcp.tools.milestones import register_milestone_tools
from plane_mcp.tools.modules import register_module_tools
from plane_mcp.tools.pages import register_page_tools
from plane_mcp.tools.projects import register_project_tools
from plane_mcp.tools.states import register_state_tools
from plane_mcp.tools.users import register_user_tools
from plane_mcp.tools.work_item_activities import register_work_item_activity_tools
from plane_mcp.tools.work_item_comments import register_work_item_comment_tools
from plane_mcp.tools.work_item_links import register_work_item_link_tools
from plane_mcp.tools.work_item_properties import register_work_item_property_tools
from plane_mcp.tools.work_item_relations import register_work_item_relation_tools
from plane_mcp.tools.work_item_types import register_work_item_type_tools
from plane_mcp.tools.work_items import register_work_item_tools
from plane_mcp.tools.work_logs import register_work_log_tools
from plane_mcp.tools.workspaces import register_workspace_tools

logger = logging.getLogger(__name__)

# All available modules and their register functions.
# Keys are the module names used in PLANE_MCP_MODULES env var.
_ALL_MODULES: dict[str, tuple[callable, int]] = {
    # (register_fn, tool_count)
    "projects":               (register_project_tools,              9),
    "work_items":             (register_work_item_tools,             7),
    "work_item_activities":   (register_work_item_activity_tools,    2),
    "work_item_comments":     (register_work_item_comment_tools,     5),
    "work_item_links":        (register_work_item_link_tools,        5),
    "work_item_relations":    (register_work_item_relation_tools,    3),
    "work_item_types":        (register_work_item_type_tools,        5),
    "work_item_properties":   (register_work_item_property_tools,    5),
    "work_logs":              (register_work_log_tools,              4),
    "cycles":                 (register_cycle_tools,                12),
    "modules":                (register_module_tools,               11),
    "epics":                  (register_epic_tools,                  5),
    "milestones":             (register_milestone_tools,             8),
    "initiatives":            (register_initiative_tools,            5),
    "intake":                 (register_intake_tools,                5),
    "labels":                 (register_label_tools,                 5),
    "pages":                  (register_page_tools,                  4),
    "states":                 (register_state_tools,                 5),
    "users":                  (register_user_tools,                  1),
    "workspaces":             (register_workspace_tools,             3),
}


def _get_enabled_modules() -> set[str] | None:
    """
    Read the PLANE_MCP_MODULES environment variable.

    Returns:
        None  → load all modules (default, backward-compatible)
        set   → only load the listed modules

    Examples:
        PLANE_MCP_MODULES=all                              → all 109 tools
        PLANE_MCP_MODULES=projects,work_items,states,...   → selected modules only
    """
    raw = os.getenv("PLANE_MCP_MODULES", "all").strip()
    if raw.lower() == "all":
        return None
    enabled = {m.strip() for m in raw.split(",") if m.strip()}
    unknown = enabled - _ALL_MODULES.keys()
    if unknown:
        logger.warning(
            "PLANE_MCP_MODULES contains unknown module(s): %s. "
            "Valid modules: %s",
            ", ".join(sorted(unknown)),
            ", ".join(sorted(_ALL_MODULES.keys())),
        )
    return enabled


def register_tools(mcp: FastMCP) -> None:
    """Register tools with the MCP server based on PLANE_MCP_MODULES env var.

    Set PLANE_MCP_MODULES to a comma-separated list of module names to load
    only those modules. Use 'all' (or leave unset) to load everything.

    Available modules:
        projects, work_items, work_item_activities, work_item_comments,
        work_item_links, work_item_relations, work_item_types,
        work_item_properties, work_logs, cycles, modules, epics, milestones,
        initiatives, intake, labels, pages, states, users, workspaces

    Example:
        PLANE_MCP_MODULES=projects,work_items,states,labels,users,workspaces,epics
    """
    enabled = _get_enabled_modules()

    total_tools = 0
    loaded_modules = []

    for module_name, (register_fn, tool_count) in _ALL_MODULES.items():
        if enabled is None or module_name in enabled:
            register_fn(mcp)
            loaded_modules.append(module_name)
            total_tools += tool_count

    logger.info(
        "Plane MCP: loaded %d module(s) with ~%d tool(s). "
        "Set PLANE_MCP_MODULES to restrict modules. Loaded: %s",
        len(loaded_modules),
        total_tools,
        ", ".join(loaded_modules),
    )