"""Epic-related tools for Plane MCP Server."""

from typing import get_args

from fastmcp import FastMCP
from plane import PlaneClient
from plane.models.enums import PriorityEnum
from plane.models.epics import Epic
from plane.models.work_item_types import WorkItemType
from plane.models.work_items import (
    CreateWorkItem,
    UpdateWorkItem,
)

from plane_mcp.client import get_plane_client_context


def register_epic_tools(mcp: FastMCP) -> None:
    """Register all epic-related tools with the MCP server."""

    def _get_epic_work_item_type(client: PlaneClient, workspace_slug: str, project_id: str) -> WorkItemType | None:
        """
        Finds the project's work item type that is marked as an epic.
        
        Searches the work item types for the given workspace and project and returns the first type whose `is_epic` attribute is truthy.
        
        Returns:
            WorkItemType | None: The work item type designated as an epic if found, `None` otherwise.
        """
        response = client.work_item_types.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
        )

        for work_item_type in response:
            if work_item_type.is_epic:
                return work_item_type

        return None

    @mcp.tool()
    def create_epic(
        project_id: str,
        name: str,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        point: int | None = None,
        description_html: str | None = None,
        description_stripped: str | None = None,
        priority: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        sort_order: float | None = None,
        is_draft: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        parent: str | None = None,
        state: str | None = None,
        estimate_point: str | None = None,
    ) -> Epic:
        """
        Create a new epic work item in the specified project.
        
        If `priority` is provided but not one of the allowed PriorityEnum values, it will be ignored (treated as no priority). Raises ValueError if the project has no work item type marked as an epic.
        
        Parameters:
            project_id: UUID of the project where the epic will be created.
            name: Title of the epic.
            priority: Desired priority value; ignored if not one of the allowed PriorityEnum literals.
        
        Returns:
            Epic: The created epic resource.
        
        Raises:
            ValueError: If no work item type with `is_epic=True` exists in the project.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate priority against allowed literal values
        validated_priority: PriorityEnum | None = (
            priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
        )

        epic_type = _get_epic_work_item_type(client, workspace_slug, project_id)

        if epic_type is None:
            raise ValueError("No work item type with is_epic=True found in the project")

        data = CreateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=epic_type.id,
            point=point,
            description_html=description_html,
            description_stripped=description_stripped,
            priority=validated_priority,
            start_date=start_date,
            target_date=target_date,
            sort_order=sort_order,
            is_draft=is_draft,
            external_source=external_source,
            external_id=external_id,
            parent=parent,
            state=state,
            estimate_point=estimate_point,
        )

        work_item = client.work_items.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

        return client.epics.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            epic_id=work_item.id,
        )

    @mcp.tool()
    def update_epic(
        project_id: str,
        epic_id: str,
        name: str | None = None,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        point: int | None = None,
        description_html: str | None = None,
        description_stripped: str | None = None,
        priority: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        sort_order: float | None = None,
        is_draft: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        state: str | None = None,
        estimate_point: str | None = None,
    ) -> Epic:
        """
        Update an epic by ID.

        Args:
            project_id: UUID of the project
            epic_id: UUID of the epic
            name: Epic name
            assignees: List of user IDs to assign to the epic
            labels: List of label IDs to attach to the epic
            point: Story point value
            description_html: HTML description of the epic
            description_stripped: Plain text description (stripped of HTML)
            priority: Priority level (urgent, high, medium, low, none)
            start_date: Start date (ISO 8601 format)
            target_date: Target/end date (ISO 8601 format)
            sort_order: Sort order value
            is_draft: Whether the epic is a draft
            external_source: External system source name
            external_id: External system identifier
            state: UUID of the state
            estimate_point: Estimate point value

        Returns:
            Updated Epic object
        """
        client, workspace_slug = get_plane_client_context()

        # Validate priority against allowed literal values
        valid_priorities = get_args(PriorityEnum)
        if priority is not None and priority not in valid_priorities:
            raise ValueError(f"Invalid priority '{priority}'. Must be one of: {valid_priorities}")
        validated_priority: PriorityEnum | None = priority  # type: ignore[assignment]

        data = UpdateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            point=point,
            description_html=description_html,
            description_stripped=description_stripped,
            priority=validated_priority,
            start_date=start_date,
            target_date=target_date,
            sort_order=sort_order,
            is_draft=is_draft,
            external_source=external_source,
            external_id=external_id,
            state=state,
            estimate_point=estimate_point,
        )

        work_item = client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=epic_id,
            data=data,
        )

        return client.epics.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            epic_id=work_item.id,
        )

    @mcp.tool()
    def delete_epic(
        project_id: str,
        epic_id: str,
    ) -> None:
        """
        Delete the epic identified by epic_id from the given project.
        
        Parameters:
            project_id (str): UUID of the project containing the epic.
            epic_id (str): UUID of the epic to delete.
        """
        client, workspace_slug = get_plane_client_context()

        return client.work_items.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=epic_id,
        )
