"""Work log-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import WorkItemWorkLog

from plane_mcp.client import get_plane_client_context


def register_work_log_tools(mcp: FastMCP) -> None:
    """Register all work log-related tools with the MCP server."""

    @mcp.tool()
    def list_work_logs(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemWorkLog]:
        """
        List work logs for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItemWorkLog objects
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.work_logs.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )

    @mcp.tool()
    def create_work_log(
        project_id: str,
        work_item_id: str,
        duration: int | None = None,
        description: str | None = None,
    ) -> WorkItemWorkLog:
        """
        Create a work log for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            duration: Duration of work in minutes
            description: Description of the work performed

        Returns:
            Created WorkItemWorkLog object
        """
        client, workspace_slug = get_plane_client_context()

        data: dict[str, Any] = {}
        if duration is not None:
            data["duration"] = duration
        if description is not None:
            data["description"] = description

        return client.work_items.work_logs.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )

    @mcp.tool()
    def update_work_log(
        project_id: str,
        work_item_id: str,
        work_log_id: str,
        duration: int | None = None,
        description: str | None = None,
    ) -> WorkItemWorkLog:
        """
        Update a work log for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            work_log_id: UUID of the work log
            duration: Duration of work in minutes
            description: Description of the work performed

        Returns:
            Updated WorkItemWorkLog object
        """
        client, workspace_slug = get_plane_client_context()

        data: dict[str, Any] = {}
        if duration is not None:
            data["duration"] = duration
        if description is not None:
            data["description"] = description

        return client.work_items.work_logs.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            work_log_id=work_log_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_log(
        project_id: str,
        work_item_id: str,
        work_log_id: str,
    ) -> None:
        """
        Delete a work log for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            work_log_id: UUID of the work log
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.work_logs.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            work_log_id=work_log_id,
        )
