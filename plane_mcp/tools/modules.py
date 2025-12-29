"""Module-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.enums import ModuleStatusEnum
from plane.models.modules import (
    CreateModule,
    Module,
    PaginatedArchivedModuleResponse,
    PaginatedModuleResponse,
    PaginatedModuleWorkItemResponse,
    UpdateModule,
)
from plane.models.work_items import WorkItem

from plane_mcp.client import get_plane_client_context


def register_module_tools(mcp: FastMCP) -> None:
    """Register all module-related tools with the MCP server."""

    @mcp.tool()
    def list_modules(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Module]:
        """
        List all modules in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of Module objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedModuleResponse = client.modules.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_module(
        project_id: str,
        name: str,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: ModuleStatusEnum | str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Create a new module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Module name
            description: Module description
            start_date: Module start date (ISO 8601 format)
            target_date: Module target/end date (ISO 8601 format)
            status: Module status (backlog, planned, in-progress, paused, completed, cancelled)
            lead: UUID of the user who leads the module
            members: List of user IDs who are members of the module
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created Module object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=status,
            lead=lead,
            members=members,
            external_source=external_source,
            external_id=external_id,
        )

        return client.modules.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def retrieve_module(project_id: str, module_id: str) -> Module:
        """
        Retrieve a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module

        Returns:
            Module object
        """
        client, workspace_slug = get_plane_client_context()
        return client.modules.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )

    @mcp.tool()
    def update_module(
        project_id: str,
        module_id: str,
        name: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: ModuleStatusEnum | str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Update a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
            name: Module name
            description: Module description
            start_date: Module start date (ISO 8601 format)
            target_date: Module target/end date (ISO 8601 format)
            status: Module status (backlog, planned, in-progress, paused, completed, cancelled)
            lead: UUID of the user who leads the module
            members: List of user IDs who are members of the module
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated Module object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=status,
            lead=lead,
            members=members,
            external_source=external_source,
            external_id=external_id,
        )

        return client.modules.update(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id, data=data
        )

    @mcp.tool()
    def delete_module(project_id: str, module_id: str) -> None:
        """
        Delete a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.delete(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )

    @mcp.tool()
    def list_archived_modules(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Module]:
        """
        List archived modules in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of archived Module objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedArchivedModuleResponse = client.modules.list_archived(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def add_work_items_to_module(
        project_id: str,
        module_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Add work items to a module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
            issue_ids: List of work item IDs to add to the module
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            issue_ids=issue_ids,
        )

    @mcp.tool()
    def remove_work_item_from_module(
        project_id: str,
        module_id: str,
        work_item_id: str,
    ) -> None:
        """
        Remove a work item from a module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
            work_item_id: UUID of the work item to remove
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.remove_work_item(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def list_module_work_items(
        project_id: str,
        module_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItem]:
        """
        List work items in a module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItem objects in the module
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedModuleWorkItemResponse = client.modules.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            params=params,
        )
        return response.results

    @mcp.tool()
    def archive_module(project_id: str, module_id: str) -> None:
        """
        Archive a module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.archive(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )

    @mcp.tool()
    def unarchive_module(project_id: str, module_id: str) -> None:
        """
        Unarchive a module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.unarchive(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )
