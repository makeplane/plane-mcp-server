"""Work item-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import PriorityEnum
from plane.models.query_params import RetrieveQueryParams
from plane.models.work_items import (
    AdvancedSearchResult,
    AdvancedSearchWorkItem,
    CreateWorkItem,
    UpdateWorkItem,
    WorkItem,
    WorkItemSearch,
)

from plane_mcp.client import get_plane_client_context


def _build_advanced_search_filters(
    *,
    assignee_ids: list[str] | None = None,
    state_ids: list[str] | None = None,
    state_groups: list[str] | None = None,
    priorities: list[str] | None = None,
    label_ids: list[str] | None = None,
    type_ids: list[str] | None = None,
    cycle_ids: list[str] | None = None,
    module_ids: list[str] | None = None,
    is_archived: bool | None = None,
    created_by_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Build an AND filter dict from flat filter params."""
    conditions: list[dict[str, Any]] = []
    if assignee_ids:
        conditions.append({"assignee_id__in": assignee_ids})
    if state_ids:
        conditions.append({"state_id__in": state_ids})
    if state_groups:
        conditions.append({"state_group__in": state_groups})
    if priorities:
        conditions.append({"priority__in": priorities})
    if label_ids:
        conditions.append({"label_id__in": label_ids})
    if type_ids:
        conditions.append({"type_id__in": type_ids})
    if cycle_ids:
        conditions.append({"cycle_id__in": cycle_ids})
    if module_ids:
        conditions.append({"module_id__in": module_ids})
    if is_archived is not None:
        conditions.append({"is_archived": is_archived})
    if created_by_ids:
        conditions.append({"created_by_id__in": created_by_ids})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"and": conditions}


def register_work_item_tools(mcp: FastMCP) -> None:
    """
    Create a new work item in a project.
    
    If `priority` is not one of the allowed PriorityEnum literal values it will be ignored (treated as unset).
    
    Returns:
        Created WorkItem object
    """
    """
    Update fields of an existing work item.
    
    If `priority` is not one of the allowed PriorityEnum literal values it will be ignored (treated as unset).
    
    Returns:
        Updated WorkItem object
    """
    """
    Delete a work item by ID.
    """
    """
    Search work items across the current workspace using a free-form text `query` that matches fields like name and description.
    
    Returns:
        WorkItemSearch object containing matching work items and related metadata
    """

    @mcp.tool()
    def create_work_item(
        project_id: str,
        name: str,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str | None = None,
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
        type: str | None = None,
    ) -> WorkItem:
        """
        Create a new work item in the specified project.
        
        Parameters:
            project_id (str): UUID of the project.
            name (str): Title of the work item.
            assignees (list[str] | None): List of user IDs to assign to the work item.
            labels (list[str] | None): List of label IDs to attach to the work item.
            type_id (str | None): UUID of the work item type.
            point (int | None): Story point value.
            description_html (str | None): HTML description of the work item.
            description_stripped (str | None): Plain-text description.
            priority (str | None): Priority level; if not one of the allowed PriorityEnum values, the priority will be ignored.
            start_date (str | None): Start date in ISO 8601 format.
            target_date (str | None): Target/end date in ISO 8601 format.
            sort_order (float | None): Sort order value.
            is_draft (bool | None): Whether the work item is a draft.
            external_source (str | None): External system source name.
            external_id (str | None): External system identifier.
            parent (str | None): UUID of the parent work item.
            state (str | None): UUID of the state.
            estimate_point (str | None): Estimate point value.
            type (str | None): Work item type identifier.
        
        Returns:
            WorkItem: The created work item.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate priority against allowed literal values
        validated_priority: PriorityEnum | None = (
            priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
        )

        data = CreateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
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
            type=type,
        )

        return client.work_items.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_work_item(
        project_id: str,
        work_item_id: str,
        name: str | None = None,
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str | None = None,
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
        type: str | None = None,
    ) -> WorkItem:
        """
        Update fields of an existing work item.
        
        Parameters:
            project_id (str): UUID of the project containing the work item.
            work_item_id (str): UUID of the work item to update.
            priority (str | None): Priority level, e.g. "urgent", "high", "medium", "low", "none".
            start_date (str | None): Start date in ISO 8601 format.
            target_date (str | None): Target/end date in ISO 8601 format.
            parent (str | None): UUID of the parent work item, if any.
        
        Returns:
            WorkItem: The updated work item object.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate priority against allowed literal values
        validated_priority: PriorityEnum | None = (
            priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
        )

        data = UpdateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
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
            type=type,
        )

        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item(project_id: str, work_item_id: str) -> None:
        """
        Delete a work item by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id)

    @mcp.tool()
    def search_work_items(
        query: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemSearch:
        """
        Search work items across a workspace.

        Args:
            workspace_slug: The workspace slug identifier
            query: This is a free-form text search and will be used to search the work items
                    by name, description etc.
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by. Prefix with '-' for descending order

        Returns:
            WorkItemSearch object containing search results
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        return client.work_items.search(workspace_slug=workspace_slug, query=query, params=params)
