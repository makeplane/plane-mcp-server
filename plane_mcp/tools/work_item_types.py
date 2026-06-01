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
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemType]:
        """
        List work item types. Omit project_id for workspace-level types.

        Each result's `id` is the `work_item_type_id` needed by list_work_item_properties
        to look up custom property and option UUIDs for PQL cf[] filters.
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            return client.work_item_types.list(
                workspace_slug=workspace_slug, project_id=project_id, params=params
            )
        return client.workspace_work_item_types.list(workspace_slug=workspace_slug)

    @mcp.tool()
    def create_work_item_type(
        name: str,
        project_id: str | None = None,
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
            name: Work item type name
            project_id: UUID of the project. Omit for workspace-level type.
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

        if project_id:
            return client.work_item_types.create(
                workspace_slug=workspace_slug, project_id=project_id, data=data
            )
        return client.workspace_work_item_types.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
    ) -> WorkItemType:
        """
        Retrieve a work item type by ID.

        Args:
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.

        Returns:
            WorkItemType object
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            return client.work_item_types.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_id=work_item_type_id,
            )
        return client.workspace_work_item_types.retrieve(
            workspace_slug=workspace_slug,
            type_id=work_item_type_id,
        )

    @mcp.tool()
    def update_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
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
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.
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

        if project_id:
            return client.work_item_types.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_id=work_item_type_id,
                data=data,
            )
        return client.workspace_work_item_types.update(
            workspace_slug=workspace_slug,
            type_id=work_item_type_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
    ) -> None:
        """
        Delete a work item type by ID.

        Args:
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            client.work_item_types.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_id=work_item_type_id,
            )
        else:
            client.workspace_work_item_types.delete(
                workspace_slug=workspace_slug,
                type_id=work_item_type_id,
            )
