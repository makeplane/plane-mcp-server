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
    def list_work_item_types(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemType]:
        """
        List all work item types in a project.

        Args:
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItemType objects
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_types.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )

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
        Create a new work item type.

        Args:
            project_id: UUID of the project
            name: Work item type name
            description: Work item type description
            project_ids: List of project IDs this type applies to
            is_epic: Whether this is an epic type
            is_active: Whether the type is active
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created WorkItemType object
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

        return client.work_item_types.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def retrieve_work_item_type(
        project_id: str,
        work_item_type_id: str,
    ) -> WorkItemType:
        """
        Retrieve a work item type by ID.

        Args:
            project_id: UUID of the project
            work_item_type_id: UUID of the work item type

        Returns:
            WorkItemType object
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_types.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_id=work_item_type_id,
        )

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
        Update a work item type by ID.

        Args:
            project_id: UUID of the project
            work_item_type_id: UUID of the work item type
            name: Work item type name
            description: Work item type description
            project_ids: List of project IDs this type applies to
            is_epic: Whether this is an epic type
            is_active: Whether the type is active
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated WorkItemType object
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
