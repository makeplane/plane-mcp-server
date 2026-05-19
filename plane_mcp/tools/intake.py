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

        return client.intake.create(workspace_slug=workspace_slug, project_id=project_id, data=intake_data)

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
        status: int | None = None,
        snoozed_till: str | None = None,
        duplicate_to: str | None = None,
        source: str | None = None,
        source_email: str | None = None,
    ) -> IntakeWorkItem:
        """
        Update an intake work item, including triage status.

        Status values:
            -2 = pending (default/unreviewed)
            -1 = declined
             0 = snoozed (requires snoozed_till date)
             1 = accepted (converts intake item to active work item)
             2 = duplicate (requires duplicate_to work item ID)

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item (use the issue field from
                IntakeWorkItem response, not the intake work item ID)
            status: Triage status (-2=pending, -1=declined, 0=snoozed, 1=accepted, 2=duplicate)
            snoozed_till: ISO 8601 date string, required when status=0
            duplicate_to: UUID of the work item this duplicates, required when status=2
            source: Source identifier
            source_email: Source email address

        Returns:
            Updated IntakeWorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        intake_data = UpdateIntakeWorkItem(
            status=status,
            snoozed_till=snoozed_till,
            duplicate_to=duplicate_to,
            source=source,
            source_email=source_email,
        )
        if status is not None or snoozed_till is not None or duplicate_to is not None:
            return client.intake.update_status(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=intake_data,
            )
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
