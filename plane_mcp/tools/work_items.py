"""Work item-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import PriorityEnum
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams
from plane.models.work_items import (
    AdvancedSearchResult,
    AdvancedSearchWorkItem,
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItem,
    WorkItemDetail,
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
    """Register all work item-related tools with the MCP server."""

    @mcp.tool()
    def list_work_items(
        project_id: str | None = None,
        query: str | None = None,
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
        workspace_search: bool = False,
        limit: int | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
        expand: str | None = None,
        fields: str | None = None,
        order_by: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> list[WorkItem] | list[AdvancedSearchResult]:
        """
        List work items in a project or search across the workspace.

        When any filter parameter is provided (assignee_ids, state_ids, state_groups,
        priorities, label_ids, type_ids, cycle_ids, module_ids, is_archived,
        created_by_ids, or query), this uses the advanced search endpoint which
        supports powerful filtering. Otherwise it uses the standard list endpoint.

        Args:
            project_id: UUID of the project. Required when no filters are provided.
                Optional when using filters (omit for workspace-wide search).
            query: Free-form text search across work item name and description
            assignee_ids: List of user UUIDs to filter by assignee
            state_ids: List of state UUIDs to filter by state
            state_groups: List of state groups to filter by
                (backlog, unstarted, started, completed, cancelled)
            priorities: List of priority values to filter by
                (urgent, high, medium, low, none)
            label_ids: List of label UUIDs to filter by label
            type_ids: List of work item type UUIDs to filter by type
            cycle_ids: List of cycle UUIDs to filter by cycle
            module_ids: List of module UUIDs to filter by module
            is_archived: Filter by archived status (true/false)
            created_by_ids: List of user UUIDs to filter by creator
            workspace_search: When true, search across all projects in the workspace.
                Only used with filters. Defaults to false.
            limit: Maximum number of results (only used with filters, default 25)
            cursor: Pagination cursor for getting next set of results (list only)
            per_page: Number of results per page, 1-100 (list only)
            expand: Comma-separated list of related fields to expand in response
                (list only, e.g. "assignees,labels,state")
            fields: Comma-separated list of fields to include in response (list only)
            order_by: Field to order results by, prefix with '-' for descending (list only)
            external_id: External system identifier for filtering (list only)
            external_source: External system source name for filtering (list only)

        Returns:
            List of WorkItem objects (unfiltered) or AdvancedSearchResult objects (filtered)
        """
        client, workspace_slug = get_plane_client_context()

        filters = _build_advanced_search_filters(
            assignee_ids=assignee_ids,
            state_ids=state_ids,
            state_groups=state_groups,
            priorities=priorities,
            label_ids=label_ids,
            type_ids=type_ids,
            cycle_ids=cycle_ids,
            module_ids=module_ids,
            is_archived=is_archived,
            created_by_ids=created_by_ids,
        )

        if filters is not None or query is not None:
            data = AdvancedSearchWorkItem(
                query=query,
                filters=filters,
                limit=limit,
                project_id=project_id,
                workspace_search=workspace_search or None,
            )
            return client.work_items.advanced_search(
                workspace_slug=workspace_slug,
                data=data,
            )

        if project_id is None:
            raise ValueError("project_id is required when no filters are provided")

        params = WorkItemQueryParams(
            cursor=cursor,
            per_page=per_page,
            expand=expand,
            fields=fields,
            order_by=order_by,
            external_id=external_id,
            external_source=external_source,
        )

        response: PaginatedWorkItemResponse = client.work_items.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            params=params,
        )

        return response.results

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
        Create a new work item.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Work item name (required)
            assignees: List of user IDs to assign to the work item
            labels: List of label IDs to attach to the work item
            type_id: UUID of the work item type
            point: Story point value
            description_html: HTML description of the work item
            description_stripped: Plain text description (stripped of HTML)
            priority: Priority level (urgent, high, medium, low, none)
            start_date: Start date (ISO 8601 format)
            target_date: Target/end date (ISO 8601 format)
            sort_order: Sort order value
            is_draft: Whether the work item is a draft
            external_source: External system source name
            external_id: External system identifier
            parent: UUID of the parent work item
            state: UUID of the state
            estimate_point: Estimate point value
            type: Work item type identifier

        Returns:
            Created WorkItem object
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
    def retrieve_work_item(
        project_id: str,
        work_item_id: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemDetail:
        """
        Retrieve a work item by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by (typically not used for single item retrieval)

        Returns:
            WorkItemDetail object with expanded relationships
        """
        client, workspace_slug = get_plane_client_context()

        # Always expand assignees to get UserLite objects instead of bare UUIDs.
        if expand:
            if "assignees" not in expand:
                expand = f"{expand},assignees"
        else:
            expand = "assignees"

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        return client.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )

    @mcp.tool()
    def retrieve_work_item_by_identifier(
        project_identifier: str,
        issue_identifier: int,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemDetail:
        """
        Retrieve a work item by project identifier and issue sequence number.

        Args:
            workspace_slug: The workspace slug identifier
            project_identifier: Project identifier string (e.g., "MP" for "My Project")
            issue_identifier: Issue sequence number (e.g., 1, 2, 3)
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated list of fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by (typically not used for single item retrieval)

        Returns:
            WorkItemDetail object with expanded relationships
        """
        client, workspace_slug = get_plane_client_context()

        # Always expand assignees to get UserLite objects instead of bare UUIDs.
        if expand:
            if "assignees" not in expand:
                expand = f"{expand},assignees"
        else:
            expand = "assignees"

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        return client.work_items.retrieve_by_identifier(
            workspace_slug=workspace_slug,
            project_identifier=project_identifier,
            issue_identifier=issue_identifier,
            params=params,
        )

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
        Update a work item by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            work_item_id: UUID of the work item
            name: Work item name
            assignees: List of user IDs to assign to the work item
            labels: List of label IDs to attach to the work item
            type_id: UUID of the work item type
            point: Story point value
            description_html: HTML description of the work item
            description_stripped: Plain text description (stripped of HTML)
            priority: Priority level (urgent, high, medium, low, none)
            start_date: Start date (ISO 8601 format)
            target_date: Target/end date (ISO 8601 format)
            sort_order: Sort order value
            is_draft: Whether the work item is a draft
            external_source: External system source name
            external_id: External system identifier
            parent: UUID of the parent work item
            state: UUID of the state
            estimate_point: Estimate point value
            type: Work item type identifier

        Returns:
            Updated WorkItem object
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
