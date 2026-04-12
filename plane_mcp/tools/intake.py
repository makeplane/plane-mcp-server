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
        Create an intake work item in the specified project.
        
        Parameters:
            project_id (str): UUID of the target project.
            data (dict[str, Any]): Mapping of fields for the new intake work item; must conform to the CreateIntakeWorkItem schema.
        
        Returns:
            IntakeWorkItem: The created intake work item.
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
        Update an existing intake work item for a project.
        
        Parameters:
            work_item_id (str): The work item identifier as exposed in the intake response's `issue` field (use this value rather than any internal intake work item ID).
            data (dict[str, Any]): Mapping of intake work item fields to update; the payload will be validated against the intake update schema.
        
        Returns:
            IntakeWorkItem: The updated intake work item.
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
