"""Work item-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.enums import PriorityEnum
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams
from plane.models.work_items import (
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItemDetail,
    WorkItemSearch,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.models import (
    AssigneeSummary,
    LabelSummary,
    WorkItemFull,
    WorkItemSummary,
    strip_html,
)


def register_work_item_tools(mcp: FastMCP) -> None:
    """Register all work item-related tools with the MCP server."""

    @mcp.tool()
    def list_work_items(
        project_id: str,
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> list[WorkItemSummary]:
        """
        List all work items in a project.

        Args:
            project_id: UUID of the project
            cursor: Pagination cursor for getting next set of results
            per_page: Number of results per page (1-100)
            order_by: Field to order results by. Prefix with '-' for descending order
            external_id: External system identifier for filtering or lookup
            external_source: External system source name for filtering or lookup

        Returns:
            List of WorkItemSummary objects (no description HTML — use retrieve_work_item for full detail).
        """
        client, workspace_slug = get_plane_client_context()

        params = WorkItemQueryParams(
            cursor=cursor,
            per_page=per_page,
            order_by=order_by,
            external_id=external_id,
            external_source=external_source,
        )

        response: PaginatedWorkItemResponse = client.work_items.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            params=params,
        )

        return [_to_work_item_summary(item).slim() for item in response.results]

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
        priority: PriorityEnum | str | None = None,
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
    ) -> WorkItemSummary:
        """
        Create a new work item.

        Args:
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
            Created WorkItemSummary object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
            point=point,
            description_html=description_html,
            description_stripped=description_stripped,
            priority=priority,
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

        item = client.work_items.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )
        return _to_work_item_summary(item).slim()

    @mcp.tool()
    def retrieve_work_item(
        project_id: str,
        work_item_id: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemFull:
        """
        Retrieve a work item by ID.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by (typically not used for single item retrieval)

        Returns:
            WorkItemFull with plain-text description and expanded assignees/labels.
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        detail: WorkItemDetail = client.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return _to_work_item_full(detail).slim()

    @mcp.tool()
    def retrieve_work_item_by_identifier(
        project_identifier: str,
        issue_identifier: int,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemFull:
        """
        Retrieve a work item by project identifier and issue sequence number.

        Args:
            project_identifier: Project identifier string (e.g., "MP" for "My Project")
            issue_identifier: Issue sequence number (e.g., 1, 2, 3)
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated list of fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by (typically not used for single item retrieval)

        Returns:
            WorkItemFull with plain-text description and expanded assignees/labels.
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
            order_by=order_by,
        )

        detail: WorkItemDetail = client.work_items.retrieve_by_identifier(
            workspace_slug=workspace_slug,
            project_identifier=project_identifier,
            issue_identifier=issue_identifier,
            params=params,
        )
        return _to_work_item_full(detail).slim()

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
        priority: PriorityEnum | str | None = None,
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
    ) -> WorkItemSummary:
        """
        Update a work item by ID.

        Args:
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
            Updated WorkItemSummary object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateWorkItem(
            name=name,
            assignees=assignees,
            labels=labels,
            type_id=type_id,
            point=point,
            description_html=description_html,
            description_stripped=description_stripped,
            priority=priority,
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

        item = client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )
        return _to_work_item_summary(item).slim()

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
        client.work_items.delete(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )

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


def _enum_str(v) -> str | None:
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def _to_work_item_summary(item) -> WorkItemSummary:
    assignees = getattr(item, "assignees", None) or []
    labels = getattr(item, "labels", None) or []
    assignee_ids = [a if isinstance(a, str) else a.id for a in assignees if a]
    label_ids = [lb if isinstance(lb, str) else lb.id for lb in labels if lb]
    return WorkItemSummary(
        id=item.id,
        sequence_id=item.sequence_id,
        name=item.name,
        priority=_enum_str(item.priority),
        state=item.state if isinstance(item.state, str) else getattr(item.state, "id", None),
        assignees=[a for a in assignee_ids if a],
        labels=[lb for lb in label_ids if lb],
    )


def _to_work_item_full(detail: WorkItemDetail) -> WorkItemFull:
    assignees = [
        AssigneeSummary(id=a.id, display_name=a.display_name)
        for a in (detail.assignees or [])
    ]
    labels = [
        LabelSummary(id=lb.id, name=lb.name, color=lb.color)
        for lb in (detail.labels or [])
    ]
    state = detail.state if isinstance(detail.state, str) else getattr(detail.state, "id", None)
    return WorkItemFull(
        id=detail.id,
        sequence_id=detail.sequence_id,
        name=detail.name,
        description=strip_html(detail.description_html),
        priority=_enum_str(detail.priority),
        state=state,
        assignees=assignees,
        labels=labels,
        parent=detail.parent,
        start_date=detail.start_date,
        target_date=detail.target_date,
    )
