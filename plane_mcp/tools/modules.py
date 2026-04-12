"""Module-related tools for Plane MCP Server."""

from typing import get_args

from fastmcp import FastMCP
from plane.models.enums import ModuleStatusEnum
from plane.models.modules import (
    CreateModule,
    Module,
    UpdateModule,
)

from plane_mcp.client import get_plane_client_context


def register_module_tools(mcp: FastMCP) -> None:
    """Register all module-related tools with the MCP server."""
    """
    Create a new module in the specified project.
    
    Parameters:
        project_id (str): UUID of the project.
        name (str): Module name.
        description (str | None): Module description.
        start_date (str | None): Module start date in ISO 8601 format.
        target_date (str | None): Module target/end date in ISO 8601 format.
        status (str | None): Module status; allowed values: "backlog", "planned", "in-progress", "paused", "completed", "cancelled".
        lead (str | None): UUID of the user who leads the module.
        members (list[str] | None): List of user IDs who are members of the module.
        external_source (str | None): External system source name.
        external_id (str | None): External system identifier.
    
    Returns:
        Module: The created module.
    """
    """
    Update an existing module by ID.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module to update.
        name (str | None): New module name.
        description (str | None): New module description.
        start_date (str | None): Module start date in ISO 8601 format.
        target_date (str | None): Module target/end date in ISO 8601 format.
        status (str | None): Module status; allowed values: "backlog", "planned", "in-progress", "paused", "completed", "cancelled".
        lead (str | None): UUID of the user who leads the module.
        members (list[str] | None): List of user IDs who are members of the module.
        external_source (str | None): External system source name.
        external_id (str | None): External system identifier.
    
    Returns:
        Module: The updated module.
    """
    """
    Delete a module by ID.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module to delete.
    """
    """
    Add work items to a module.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module.
        issue_ids (list[str]): List of work item IDs to add to the module.
    """
    """
    Remove a work item from a module.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module.
        work_item_id (str): UUID of the work item to remove.
    """
    """
    Archive a module.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module to archive.
    """
    """
    Unarchive a module.
    
    Parameters:
        project_id (str): UUID of the project.
        module_id (str): UUID of the module to unarchive.
    """

    @mcp.tool()
    def create_module(
        project_id: str,
        name: str,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Create a new module within the specified project.
        
        If `status` is not one of the allowed ModuleStatusEnum values, the module's status will be left unset.
        
        Parameters:
            project_id (str): UUID of the project to contain the module.
            name (str): Module name.
            description (str | None): Module description.
            start_date (str | None): Module start date in ISO 8601 format.
            target_date (str | None): Module target/end date in ISO 8601 format.
            status (str | None): Desired module status; valid values are the members of `ModuleStatusEnum`. If invalid, the status will be ignored.
            lead (str | None): UUID of the user who leads the module.
            members (list[str] | None): List of user UUIDs who are members of the module.
            external_source (str | None): External system source name.
            external_id (str | None): External system identifier.
        
        Returns:
            Module: The created Module.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate status against allowed literal values
        validated_status: ModuleStatusEnum | None = (
            status if status in get_args(ModuleStatusEnum) else None  # type: ignore[assignment]
        )

        data = CreateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=validated_status,
            lead=lead,
            members=members,
            external_source=external_source,
            external_id=external_id,
        )

        return client.modules.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_module(
        project_id: str,
        module_id: str,
        name: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Update an existing module in the current workspace.
        
        If `status` is not one of the allowed values ("backlog", "planned", "in-progress", "paused", "completed", "cancelled"), it will be ignored (no status change). Other provided fields will be applied to the module identified by `project_id` and `module_id`.
        
        Parameters:
            project_id (str): UUID of the project containing the module.
            module_id (str): UUID of the module to update.
            name (str | None): New module name.
            description (str | None): New module description.
            start_date (str | None): Module start date (ISO 8601).
            target_date (str | None): Module target/end date (ISO 8601).
            status (str | None): Module status; allowed values are "backlog", "planned", "in-progress", "paused", "completed", "cancelled".
            lead (str | None): UUID of the user who leads the module.
            members (list[str] | None): List of user UUIDs who are members of the module.
            external_source (str | None): External system source name.
            external_id (str | None): External system identifier.
        
        Returns:
            Module: The updated Module object.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate status against allowed literal values
        validated_status: ModuleStatusEnum | None = (
            status if status in get_args(ModuleStatusEnum) else None  # type: ignore[assignment]
        )

        data = UpdateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=validated_status,
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
        Delete a module from a project in the current workspace.
        
        Parameters:
            project_id (str): UUID of the project containing the module.
            module_id (str): UUID of the module to delete.
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.delete(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)

    @mcp.tool()
    def add_work_items_to_module(
        project_id: str,
        module_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Add the given work items to the specified module within the current workspace.
        
        Parameters:
            project_id (str): UUID of the project containing the module.
            module_id (str): UUID of the module to update.
            issue_ids (list[str]): List of work item IDs to add to the module.
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
        Remove a work item from a module in the current workspace.
        
        Parameters:
            project_id (str): UUID of the project containing the module.
            module_id (str): UUID of the module to update.
            work_item_id (str): UUID of the work item to remove from the module.
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.remove_work_item(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def archive_module(project_id: str, module_id: str) -> None:
        """
        Archive a module in the current Plane workspace.
        
        Parameters:
            project_id (str): UUID of the project containing the module.
            module_id (str): UUID of the module to archive.
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.archive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)

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
        client.modules.unarchive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
