"""Work item-related tools for Plane MCP Server."""

from typing import Annotated, Any, get_args

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.enums import PriorityEnum
from plane.models.query_params import RetrieveQueryParams, WorkItemCountQueryParams, WorkItemQueryParams
from plane.models.work_items import (
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItem,
    WorkItemDetail,
    WorkItemGroupedCountResponse,
    WorkItemSearch,
)
from pydantic import Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.pql_reference import PQL_FIELD_HINT, PQL_FULL_REFERENCE

logger = get_logger(__name__)


def register_work_item_tools(mcp: FastMCP) -> None:
    """Register all work item-related tools with the MCP server."""

    @mcp.tool()
    def list_work_items(
        project_id: str | None = None,
        pql: Annotated[str | None, Field(description=PQL_FIELD_HINT)] = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> dict[str, Any]:
        """
        List work items with optional PQL filtering.

        Omit project_id to list across the entire workspace.
        Pass project_id to scope results to a single project.

        For UUID fields (assignee, state, label, cycle, module, type,
        milestone) call the relevant list tool first to get the UUID.

        Args:
            project_id: UUID of the project. Omit for workspace-wide results.
            pql: PQL filter. See field description for syntax.
            order_by: Sort field; prefix `-` for descending (e.g. `-created_at`).
            per_page: 1-100, default 25.
            cursor: From previous response's next_cursor.
            expand: Comma-separated relations to expand (e.g. assignees,labels,state).
            fields: Sparse fieldset — id, name, sequence_id, priority, state,
                project, assignees, labels, type_id, start_date, target_date,
                created_at, updated_at, created_by, is_draft. Use `project` not
                `project_id`.
            external_id / external_source: Filter by external system.

        Returns:
            results: Paginated list of work items.
            total_count: True DB total, not page-bounded — use for counts.
            next_cursor: Cursor for the next page.
            prev_cursor: Cursor for the previous page.
        """
        client, workspace_slug = get_plane_client_context()

        params = WorkItemQueryParams(
            pql=pql,
            order_by=order_by,
            per_page=per_page,
            cursor=cursor,
            expand=expand,
            fields=fields,
            external_id=external_id,
            external_source=external_source,
        )

        try:
            if project_id:
                response: PaginatedWorkItemResponse = client.work_items.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params,
                )
            else:
                response = client.work_items.list_workspace(
                    workspace_slug=workspace_slug,
                    params=params,
                )
        except HttpError as e:
            if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
                logger.warning("list_work_items: invalid PQL %r → %s", pql, e.response)
                return {
                    "error": e.response["pql"],
                    "failed_pql": pql,
                    "pql_reference": PQL_FULL_REFERENCE,
                    "hint": "The PQL above failed. Fix it using the reference and retry list_work_items.",
                }
            raise

        return {
            "results": [item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
        }

    @mcp.tool()
    def count_work_items(
        pql: Annotated[str | None, Field(description=PQL_FIELD_HINT)] = None,
        group_by: str | None = None,
        sub_group_by: str | None = None,
    ) -> dict[str, Any]:
        """
        Count work items across the workspace with optional grouping.

        Use this for analytics — "how many urgent items?", "distribution by state?" —
        without fetching full work item payloads.

        Args:
            pql: PQL filter to scope the count (e.g. 'priority = "urgent"').
            group_by: Dimension to group counts by. Supported values:
                state_id, state__group, priority, project_id, type_id,
                labels__id, assignees__id, issue_module__module_id,
                release_work_items__release_id, cycle_id, milestone_id,
                created_by, target_date, start_date.
            sub_group_by: Second dimension for nested grouping (requires group_by).

        Returns:
            grouped_by: The group_by field used (null if none).
            sub_grouped_by: The sub_group_by field used (null if none).
            total_count: Total matching work items.
            grouped_counts: Dict of group_key → {count} or
                {total_count, sub_grouped_counts} when sub_group_by is set.
                Keys are UUIDs for FK fields, plain strings for priority/state__group,
                ISO dates for target_date/start_date, "None" for unset values.
        """
        client, workspace_slug = get_plane_client_context()
        params = WorkItemCountQueryParams(pql=pql, group_by=group_by, sub_group_by=sub_group_by)
        response: WorkItemGroupedCountResponse = client.work_items.count_workspace(
            workspace_slug=workspace_slug,
            params=params,
        )
        return response.model_dump()

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
            project_id: UUID of the project
            work_item_id: UUID of the work item
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by

        Returns:
            WorkItemDetail object with expanded relationships
        """
        client, workspace_slug = get_plane_client_context()

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
        work_item_identifier: str,
        expand: str | None = None,
        fields: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        order_by: str | None = None,
    ) -> WorkItemDetail:
        """
        Retrieve a work item by its full identifier (project prefix + sequence number).

        The identifier must be in PROJECT-N format where PROJECT is the project's
        identifier string and N is the sequence number. Both parts are required.

        Valid sparse `fields` values include: id, name, sequence_id, priority,
        state, project, workspace, parent, assignees, labels, type_id,
        start_date, target_date, created_at, updated_at, created_by,
        updated_by, is_draft, external_source, external_id, estimate_point.
        Use `project` (not `project_id`) to get the project UUID.

        If you need the project UUID from a short identifier like "SHO",
        use `list_projects()` instead — it returns `id` and `identifier`
        for every project.

        Args:
            work_item_identifier: Full work item identifier in PROJECT-N format
            expand: Comma-separated fields to expand (e.g., "assignees,labels,state")
            fields: Comma-separated sparse fieldset (see valid values above)
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by

        Returns:
            WorkItemDetail object with expanded relationships
        """
        parts = work_item_identifier.rsplit("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            raise ValueError(
                f"Invalid work item identifier {work_item_identifier!r}. "
                "Expected PROJECT-N format where N is the sequence number."
            )
        project_identifier, sequence_str = parts
        client, workspace_slug = get_plane_client_context()

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
            issue_identifier=int(sequence_str),
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
            project_id: UUID of the project
            work_item_id: UUID of the work item
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id)

    @mcp.tool()
    def add_work_item_assignee(project_id: str, work_item_id: str, user_id: str) -> WorkItem:
        """
        Add an assignee to a work item without removing existing assignees.

        Use this instead of update_work_item when you only want to add one
        assignee — update_work_item replaces the full assignees list.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            user_id: UUID of the user to add as assignee

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        current_ids = [u.id for u in (current.assignees or []) if u.id]
        if user_id not in current_ids:
            current_ids.append(user_id)
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(assignees=current_ids),
        )

    @mcp.tool()
    def remove_work_item_assignee(project_id: str, work_item_id: str, user_id: str) -> WorkItem:
        """
        Remove an assignee from a work item without affecting other assignees.

        Use this instead of update_work_item when you only want to remove one
        assignee — update_work_item replaces the full assignees list.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            user_id: UUID of the user to remove

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        current_ids = [u.id for u in (current.assignees or []) if u.id and u.id != user_id]
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(assignees=current_ids),
        )

    @mcp.tool()
    def add_work_item_label(project_id: str, work_item_id: str, label_id: str) -> WorkItem:
        """
        Add a label to a work item without removing existing labels.

        Use this instead of update_work_item when you only want to add one
        label — update_work_item replaces the full labels list.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            label_id: UUID of the label to add

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        current_ids = [lb.id for lb in (current.labels or []) if lb.id]
        if label_id not in current_ids:
            current_ids.append(label_id)
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(labels=current_ids),
        )

    @mcp.tool()
    def remove_work_item_label(project_id: str, work_item_id: str, label_id: str) -> WorkItem:
        """
        Remove a label from a work item without affecting other labels.

        Use this instead of update_work_item when you only want to remove one
        label — update_work_item replaces the full labels list.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            label_id: UUID of the label to remove

        Returns:
            Updated WorkItem object
        """
        client, workspace_slug = get_plane_client_context()
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        current_ids = [lb.id for lb in (current.labels or []) if lb.id and lb.id != label_id]
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(labels=current_ids),
        )

    @mcp.tool()
    def list_archived_work_items(
        project_id: str,
        pql: Annotated[str | None, Field(description=PQL_FIELD_HINT)] = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """
        List archived work items in a project with optional PQL filtering.

        Args:
            project_id: UUID of the project
            pql: PQL filter expression. Omit to list all archived items.
            order_by: Field to sort by; prefix with `-` for descending
                (default `-archived_at`).
            per_page: Results per page, 1-100 (default 100).
            cursor: Pagination cursor from a previous response's `next_cursor`.
            expand: Comma-separated related fields to expand.
            fields: Comma-separated sparse fieldset.

        Returns:
            Paginated envelope with results, total_count, next_cursor, prev_cursor.
        """
        client, workspace_slug = get_plane_client_context()
        params = WorkItemQueryParams(
            pql=pql,
            order_by=order_by,
            per_page=per_page,
            cursor=cursor,
            expand=expand,
            fields=fields,
        )
        try:
            response = client.work_items.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params,
            )
        except HttpError as e:
            if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
                logger.warning("list_archived_work_items: invalid PQL %r → %s", pql, e.response)
                return {
                    "error": e.response["pql"],
                    "failed_pql": pql,
                    "pql_reference": PQL_FULL_REFERENCE,
                    "hint": "The PQL above failed. Fix it using the reference and retry list_archived_work_items.",
                }
            raise
        return {
            "results": [item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
        }

    @mcp.tool()
    def archive_work_item(project_id: str, work_item_id: str) -> None:
        """
        Archive a work item.

        Only work items in a completed or cancelled state can be archived.
        The work item will no longer appear in active work item lists.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item to archive
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.archive(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def unarchive_work_item(project_id: str, work_item_id: str) -> None:
        """
        Unarchive a work item.

        Restores an archived work item back to active status.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item to unarchive
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.unarchive(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
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
        Search work items by text across a workspace.

        Use this for free-text name/description search. For structured
        filtering (priority, state, assignee, dates, etc.) use
        `list_work_items` with a PQL expression.

        Args:
            query: Free-text search string across work item name and description
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response
            external_id: External system identifier for filtering
            external_source: External system source name for filtering
            order_by: Field to order results by. Prefix with '-' for descending

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
