"""Cycle-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.cycles import (
    CreateCycle,
    Cycle,
    TransferCycleWorkItemsRequest,
    UpdateCycle,
)

from plane_mcp.client import get_plane_client_context


def register_cycle_tools(mcp: FastMCP) -> None:
    """
    Create a new cycle in the specified project.
    
    Parameters:
        project_id (str): UUID of the project to create the cycle in.
        name (str): Cycle name.
        owned_by (str): UUID of the user who will own the cycle.
        description (str | None): Optional cycle description.
        start_date (str | None): Optional start date in ISO 8601 format.
        end_date (str | None): Optional end date in ISO 8601 format.
        external_source (str | None): Optional external system source name.
        external_id (str | None): Optional external system identifier.
        timezone (str | None): Optional timezone identifier.
    
    Returns:
        Cycle: The created Cycle object.
    """
    """
    Update fields of an existing cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to update.
        name (str | None): New cycle name, if updating.
        description (str | None): New description, if updating.
        start_date (str | None): New start date in ISO 8601 format, if updating.
        end_date (str | None): New end date in ISO 8601 format, if updating.
        owned_by (str | None): UUID of the new owner, if updating.
        external_source (str | None): External system source name, if updating.
        external_id (str | None): External system identifier, if updating.
        timezone (str | None): Timezone identifier, if updating.
    
    Returns:
        Cycle: The updated Cycle object.
    """
    """
    Delete a cycle by its ID within the given project.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to delete.
    """
    """
    Add one or more work items (issues) to a cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to add work items to.
        issue_ids (list[str]): List of work item (issue) UUIDs to add.
    """
    """
    Remove a single work item from a cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to remove the work item from.
        work_item_id (str): UUID of the work item to remove.
    """
    """
    Transfer all work items from a source cycle to another target cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycles.
        cycle_id (str): UUID of the source cycle whose work items will be transferred.
        new_cycle_id (str): UUID of the target cycle to receive the work items.
    """
    """
    Archive a cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to archive.
    
    Returns:
        bool: `True` if the cycle was archived successfully, `False` otherwise.
    """
    """
    Unarchive a cycle.
    
    Parameters:
        project_id (str): UUID of the project containing the cycle.
        cycle_id (str): UUID of the cycle to unarchive.
    
    Returns:
        bool: `True` if the cycle was unarchived successfully, `False` otherwise.
    """

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
        Create a new cycle in the specified project.
        
        Parameters:
            project_id (str): UUID of the project to contain the cycle.
            name (str): Cycle name.
            owned_by (str): UUID of the user who owns the cycle.
            description (str | None): Optional cycle description.
            start_date (str | None): Optional cycle start date in ISO 8601 format (e.g., "YYYY-MM-DD" or full timestamp).
            end_date (str | None): Optional cycle end date in ISO 8601 format.
            external_source (str | None): Optional external system source name.
            external_id (str | None): Optional identifier from the external system.
            timezone (str | None): Optional IANA timezone name for the cycle (e.g., "UTC", "America/Los_Angeles").
        
        Returns:
            Cycle: The created Cycle.
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
        Update a cycle by ID, applying only the fields that are provided.
        
        Parameters:
        	project_id (str): UUID of the project containing the cycle.
        	cycle_id (str): UUID of the cycle to update.
        	name (str | None): New cycle name, or `None` to leave unchanged.
        	description (str | None): New cycle description, or `None` to leave unchanged.
        	start_date (str | None): New start date in ISO 8601 format, or `None` to leave unchanged.
        	end_date (str | None): New end date in ISO 8601 format, or `None` to leave unchanged.
        	owned_by (str | None): UUID of the user who will own the cycle, or `None` to leave unchanged.
        	external_source (str | None): External system source name, or `None` to leave unchanged.
        	external_id (str | None): External system identifier, or `None` to leave unchanged.
        	timezone (str | None): Cycle timezone, or `None` to leave unchanged.
        
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

        return client.cycles.update(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id, data=data)

    @mcp.tool()
    def delete_cycle(project_id: str, cycle_id: str) -> None:
        """
        Delete the cycle identified by `cycle_id` within the specified project.
        
        Parameters:
            project_id (str): UUID of the project containing the cycle.
            cycle_id (str): UUID of the cycle to delete.
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.delete(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

    @mcp.tool()
    def add_work_items_to_cycle(
        project_id: str,
        cycle_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Add the specified work items to the given cycle in the project.
        
        Parameters:
            project_id (str): UUID of the project containing the cycle.
            cycle_id (str): UUID of the target cycle.
            issue_ids (list[str]): List of work item IDs to add to the cycle.
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
        Remove a work item from the specified cycle in a project.
        
        Parameters:
            project_id (str): UUID of the project.
            cycle_id (str): UUID of the cycle.
            work_item_id (str): UUID of the work item to remove.
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.remove_work_item(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def transfer_cycle_work_items(
        project_id: str,
        cycle_id: str,
        new_cycle_id: str,
    ) -> None:
        """
        Transfer all work items from one cycle to another within a project.
        
        Parameters:
            project_id (str): UUID of the project containing the cycles.
            cycle_id (str): UUID of the source cycle whose work items will be moved.
            new_cycle_id (str): UUID of the destination cycle that will receive the work items.
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
        return client.cycles.archive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

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
        return client.cycles.unarchive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
