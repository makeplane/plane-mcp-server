"""Work item activity-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import (
    PaginatedWorkItemActivityResponse,
    WorkItemActivity,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_activity_tools(mcp: FastMCP) -> None:
    """Register all work item activity-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_activities(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemActivity]:
        """
        List activities for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItemActivity objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedWorkItemActivityResponse = client.work_items.activities.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return response.results

    @mcp.tool()
    def retrieve_work_item_activity(
        project_id: str,
        work_item_id: str,
        activity_id: str,
    ) -> WorkItemActivity:
        """
        Retrieve a specific activity for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            activity_id: UUID of the activity

        Returns:
            WorkItemActivity object
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.activities.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            activity_id=activity_id,
        )
