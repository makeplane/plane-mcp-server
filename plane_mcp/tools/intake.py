"""Intake work item-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.intake import (
    CreateIntakeWorkItem,
    IntakeWorkItem,
    PaginatedIntakeWorkItemResponse,
    UpdateIntakeWorkItem,
)
from plane.models.query_params import PaginatedQueryParams, RetrieveQueryParams

from plane_mcp.client import get_plane_client_context


def register_intake_tools(mcp: FastMCP) -> None:
    """Register all intake work item-related tools with the MCP server."""

    @mcp.tool()
    def list_intake_work_items(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[IntakeWorkItem]:
        """
        List all intake work items in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary (e.g., per_page, cursor)

        Returns:
            List of IntakeWorkItem objects
        """
        client, workspace_slug = get_plane_client_context()
        
        query_params = None
        if params:
            query_params = PaginatedQueryParams(**params)
        
        response: PaginatedIntakeWorkItemResponse = client.intake.list(
            workspace_slug=workspace_slug, project_id=project_id, params=query_params
        )
        return response.results

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

        return client.intake.create(
            workspace_slug=workspace_slug, project_id=project_id, data=intake_data
        )

    @mcp.tool()
    def retrieve_intake_work_item(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
    ) -> IntakeWorkItem:
        """
        Retrieve an intake work item by work item ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item (use the issue field from
                IntakeWorkItem response, not the intake work item ID)
            params: Optional query parameters as a dictionary (e.g., expand, fields)

        Returns:
            IntakeWorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        
        query_params = None
        if params:
            query_params = RetrieveQueryParams(**params)
        
        return client.intake.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=query_params,
        )

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
        client.intake.delete(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )

