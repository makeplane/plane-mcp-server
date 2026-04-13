"""Intake work item-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.intake import (
    CreateIntakeWorkItem,
    IntakeWorkItem,
    UpdateIntakeWorkItem,
)

from plane_mcp.client import get_plane_client_context


def register_intake_tools(mcp: FastMCP) -> None:
    """Register all intake work item-related tools with the MCP server."""

    @mcp.tool()
    def create_intake_work_item(
        project_id: str,
        data: dict[str, Any],
    ) -> IntakeWorkItem:
        """
        Create a new intake work item in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            data: Intake work item data as a dictionary

        Returns:
            Created IntakeWorkItem object
        """
        client, workspace_slug = get_plane_client_context()

        intake_data = CreateIntakeWorkItem(**data)

        return client.intake.create(workspace_slug=workspace_slug, project_id=project_id, data=intake_data)

    @mcp.tool()
    def update_intake_work_item(
        project_id: str,
        work_item_id: str,
        data: dict[str, Any],
    ) -> IntakeWorkItem:
        """
        Update an intake work item by work item ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item (use the issue field from
                IntakeWorkItem response, not the intake work item ID)
            data: Updated intake work item data as a dictionary

        Returns:
            Updated IntakeWorkItem object
        """
        client, workspace_slug = get_plane_client_context()

        intake_data = UpdateIntakeWorkItem(**data)

        return client.intake.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=intake_data,
        )

    @mcp.tool()
    def delete_intake_work_item(project_id: str, work_item_id: str) -> None:
        """
        Delete an intake work item by work item ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item (use the issue field from
                IntakeWorkItem response, not the intake work item ID)
        """
        client, workspace_slug = get_plane_client_context()
        client.intake.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id)
