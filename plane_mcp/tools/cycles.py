"""Cycle-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.cycles import (
    CreateCycle,
    Cycle,
    PaginatedArchivedCycleResponse,
    PaginatedCycleResponse,
    PaginatedCycleWorkItemResponse,
    TransferCycleWorkItemsRequest,
    UpdateCycle,
)
from plane.models.work_items import WorkItem

from plane_mcp.client import get_plane_client_context


def register_cycle_tools(mcp: FastMCP) -> None:
    """Register all cycle-related tools with the MCP server."""

    @mcp.tool()
    def list_cycles(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Cycle]:
        """
        List all cycles in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of Cycle objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedCycleResponse = client.cycles.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_cycle(
        project_id: str,
        name: str,
        owned_by: str,
        description: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        timezone: str | None = None,
    ) -> Cycle:
        """
        Create a new cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Cycle name
            owned_by: UUID of the user who owns the cycle
            description: Cycle description
            start_date: Cycle start date (ISO 8601 format)
            end_date: Cycle end date (ISO 8601 format)
            external_source: External system source name
            external_id: External system identifier
            timezone: Cycle timezone

        Returns:
            Created Cycle object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateCycle(
            name=name,
            owned_by=owned_by,
            description=description,
            start_date=start_date,
            end_date=end_date,
            external_source=external_source,
            external_id=external_id,
            timezone=timezone,
            project_id=project_id,
        )

        return client.cycles.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def retrieve_cycle(project_id: str, cycle_id: str) -> Cycle:
        """
        Retrieve a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle

        Returns:
            Cycle object
        """
        client, workspace_slug = get_plane_client_context()
        return client.cycles.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    @mcp.tool()
    def update_cycle(
        project_id: str,
        cycle_id: str,
        name: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        owned_by: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        timezone: str | None = None,
    ) -> Cycle:
        """
        Update a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            name: Cycle name
            description: Cycle description
            start_date: Cycle start date (ISO 8601 format)
            end_date: Cycle end date (ISO 8601 format)
            owned_by: UUID of the user who owns the cycle
            external_source: External system source name
            external_id: External system identifier
            timezone: Cycle timezone

        Returns:
            Updated Cycle object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateCycle(
            name=name,
            description=description,
            start_date=start_date,
            end_date=end_date,
            owned_by=owned_by,
            external_source=external_source,
            external_id=external_id,
            timezone=timezone,
        )

        return client.cycles.update(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id, data=data
        )

    @mcp.tool()
    def delete_cycle(project_id: str, cycle_id: str) -> None:
        """
        Delete a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.delete(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    @mcp.tool()
    def list_archived_cycles(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Cycle]:
        """
        List archived cycles in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of archived Cycle objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedArchivedCycleResponse = client.cycles.list_archived(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def add_work_items_to_cycle(
        project_id: str,
        cycle_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Add work items to a cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            issue_ids: List of work item IDs to add to the cycle
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            issue_ids=issue_ids,
        )

    @mcp.tool()
    def remove_work_item_from_cycle(
        project_id: str,
        cycle_id: str,
        work_item_id: str,
    ) -> None:
        """
        Remove a work item from a cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            work_item_id: UUID of the work item to remove
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.remove_work_item(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def list_cycle_work_items(
        project_id: str,
        cycle_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItem]:
        """
        List work items in a cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItem objects in the cycle
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedCycleWorkItemResponse = client.cycles.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            params=params,
        )
        return response.results

    @mcp.tool()
    def transfer_cycle_work_items(
        project_id: str,
        cycle_id: str,
        new_cycle_id: str,
    ) -> None:
        """
        Transfer work items from one cycle to another.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the source cycle
            new_cycle_id: UUID of the target cycle to transfer issues to
        """
        client, workspace_slug = get_plane_client_context()

        data = TransferCycleWorkItemsRequest(new_cycle_id=new_cycle_id)

        client.cycles.transfer_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=data,
        )

    @mcp.tool()
    def archive_cycle(project_id: str, cycle_id: str) -> bool:
        """
        Archive a cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle

        Returns:
            True if the cycle was archived successfully
        """
        client, workspace_slug = get_plane_client_context()
        return client.cycles.archive(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    @mcp.tool()
    def unarchive_cycle(project_id: str, cycle_id: str) -> bool:
        """
        Unarchive a cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle

        Returns:
            True if the cycle was unarchived successfully
        """
        client, workspace_slug = get_plane_client_context()
        return client.cycles.unarchive(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )
