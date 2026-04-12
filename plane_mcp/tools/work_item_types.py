"""Work item type-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.work_item_types import (
    CreateWorkItemType,
    UpdateWorkItemType,
    WorkItemType,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_type_tools(mcp: FastMCP) -> None:
    """Register all work item type-related tools with the MCP server."""

    @mcp.tool()
    def create_work_item_type(
        project_id: str,
        name: str,
        description: str | None = None,
        project_ids: list[str] | None = None,
        is_epic: bool | None = None,
        is_active: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemType:
        """
        Create a new work item type in the current workspace for the given project.
        
        Parameters:
            project_id: Project UUID where the work item type will be created.
            name: Name of the work item type.
            description: Optional human-readable description.
            project_ids: Optional list of project UUIDs this type applies to.
            is_epic: Optional flag indicating whether the type represents an epic.
            is_active: Optional flag indicating whether the type is active.
            external_source: Optional name of an external system providing this type.
            external_id: Optional identifier from the external system.
        
        Returns:
            WorkItemType: The newly created work item type.
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateWorkItemType(
            name=name,
            description=description,
            project_ids=project_ids,
            is_epic=is_epic,
            is_active=is_active,
            external_source=external_source,
            external_id=external_id,
        )

        return client.work_item_types.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_work_item_type(
        project_id: str,
        work_item_type_id: str,
        name: str | None = None,
        description: str | None = None,
        project_ids: list[str] | None = None,
        is_epic: bool | None = None,
        is_active: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemType:
        """
        Updates a work item type identified by its ID in the current Plane workspace.
        
        Parameters:
            project_id (str): UUID of the project containing the work item type.
            work_item_type_id (str): UUID of the work item type to update.
            name (str | None): New name for the work item type.
            description (str | None): New description for the work item type.
            project_ids (list[str] | None): List of project UUIDs the type applies to.
            is_epic (bool | None): Whether the type should be marked as an epic.
            is_active (bool | None): Whether the type should be active.
            external_source (str | None): External system source name.
            external_id (str | None): External system identifier.
        
        Returns:
            WorkItemType: The updated work item type.
        
        Notes:
            Only parameters provided as non-None are applied; unspecified fields are left unchanged.
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateWorkItemType(
            name=name,
            description=description,
            project_ids=project_ids,
            is_epic=is_epic,
            is_active=is_active,
            external_source=external_source,
            external_id=external_id,
        )

        return client.work_item_types.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_id=work_item_type_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_type(
        project_id: str,
        work_item_type_id: str,
    ) -> None:
        """
        Delete a work item type by ID.

        Args:
            project_id: UUID of the project
            work_item_type_id: UUID of the work item type
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_types.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_id=work_item_type_id,
        )
