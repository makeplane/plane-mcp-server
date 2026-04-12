"""Tools for Plane MCP Server."""

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
from plane_mcp.tools.unified import register_unified_tools
from plane_mcp.tools.work_items import register_work_item_tools
from plane_mcp.tools.work_logs import register_work_log_tools
from plane_mcp.tools.workspaces import register_workspace_tools


def register_tools(mcp: FastMCP) -> None:
    """
    Register all Plane MCP tools onto the provided MCP server.
    
    Registers each feature-specific toolset (unified, project, work items, work item activity, work item comments, work item links, work item relations, work logs, cycles, users, modules, initiatives, intake, labels, pages, work item properties, work item types, states, workspaces, epics, and milestones) in a fixed sequence. Exceptions raised by individual registration functions are not caught and will propagate.
    
    Parameters:
        mcp (FastMCP): The MCP server instance on which to register all tools.
    """
    register_unified_tools(mcp)
    register_project_tools(mcp)
    register_work_item_tools(mcp)
    register_work_item_activity_tools(mcp)
    register_work_item_comment_tools(mcp)
    register_work_item_link_tools(mcp)
    register_work_item_relation_tools(mcp)
    register_work_log_tools(mcp)
    register_cycle_tools(mcp)
    register_user_tools(mcp)
    register_module_tools(mcp)
    register_initiative_tools(mcp)
    register_intake_tools(mcp)
    register_label_tools(mcp)
    register_page_tools(mcp)
    register_work_item_property_tools(mcp)
    register_work_item_type_tools(mcp)
    register_state_tools(mcp)
    register_workspace_tools(mcp)
    register_epic_tools(mcp)
    register_milestone_tools(mcp)
