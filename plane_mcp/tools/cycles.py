"""Cycle-related tools for Plane MCP Server."""

from datetime import date
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.cycles import (
    CreateCycle,
    Cycle,
    PaginatedArchivedCycleResponse,
    PaginatedCycleLiteResponse,
    PaginatedCycleWorkItemResponse,
    TransferCycleWorkItemsRequest,
    UpdateCycle,
)
from plane.models.enums import CycleViewEnum
from plane.models.query_params import CycleLiteListQueryParams, LiteListQueryParams, WorkItemQueryParams
from pydantic import Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.pql_reference import PQL_FIELD_HINT, PQL_FULL_REFERENCE

logger = get_logger(__name__)


def register_cycle_tools(mcp: FastMCP) -> None:
    """Register all cycle-related tools with the MCP server."""

    @mcp.tool()
    def list_cycles(
        project_id: str,
        archived: bool = False,
        cycle_view: CycleViewEnum | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
    ) -> PaginatedCycleLiteResponse | PaginatedArchivedCycleResponse:
        """
        List cycles in a project. Active (non-archived) cycles by default.

        Args:
            project_id: UUID of the project
            archived: Set True to list archived cycles instead of active ones.
            cycle_view: Filter active cycles by status — "current" (running now),
                "upcoming" (starts later), "completed" (ended), "draft" (no dates),
                or "incomplete" (not yet finished). Ignored when archived is True.
            cursor: Pagination cursor from a previous response's next_cursor
                (form "{per_page}:{page}:{offset}"). Omit for the first page.
            per_page: Number of results per page (1-1000, default and max 1000).
            order_by: Field to order results by. Prefix with '-' for descending.

        Returns:
            Paginated envelope: results (lite cycles) + total_count,
            next_cursor, next_page_results.
        """
        client, workspace_slug = get_plane_client_context()
        if archived:
            params = LiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by)
            return client.cycles.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params.model_dump(exclude_none=True),
            )
        params = CycleLiteListQueryParams(cursor=cursor, per_page=per_page, order_by=order_by, cycle_view=cycle_view)
        return client.cycles.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)

    @mcp.tool()
    def create_cycle(
        project_id: str,
        name: str,
        owned_by: str,
        description: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        timezone: str | None = None,
    ) -> Cycle:
        """
        Create a new cycle.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Cycle name
            owned_by: UUID of the user who owns the cycle
            description: Cycle description
            start_date: Cycle start date (ISO 8601 format)
            end_date: Cycle end date (ISO 8601 format)
            external_source: External system source name
            external_id: External system identifier
            timezone: Cycle timezone

        Returns:
            Created Cycle object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateCycle(
            name=name,
            owned_by=owned_by,
            description=description,
            start_date=start_date,
            end_date=end_date,
            external_source=external_source,
            external_id=external_id,
            timezone=timezone,
            project_id=project_id,
        )

        return client.cycles.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def retrieve_cycle(project_id: str, cycle_id: str) -> Cycle:
        """
        Retrieve a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle

        Returns:
            Cycle object
        """
        client, workspace_slug = get_plane_client_context()
        return client.cycles.retrieve(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

    @mcp.tool()
    def update_cycle(
        project_id: str,
        cycle_id: str,
        name: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        owned_by: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        timezone: str | None = None,
    ) -> Cycle:
        """
        Update a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            name: Cycle name
            description: Cycle description
            start_date: Cycle start date (ISO 8601 format)
            end_date: Cycle end date (ISO 8601 format)
            owned_by: UUID of the user who owns the cycle
            external_source: External system source name
            external_id: External system identifier
            timezone: Cycle timezone

        Returns:
            Updated Cycle object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateCycle(
            name=name,
            description=description,
            start_date=start_date,
            end_date=end_date,
            owned_by=owned_by,
            external_source=external_source,
            external_id=external_id,
            timezone=timezone,
        )

        return client.cycles.update(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id, data=data)

    @mcp.tool()
    def delete_cycle(project_id: str, cycle_id: str) -> None:
        """
        Delete a cycle by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the cycle
        """
        client, workspace_slug = get_plane_client_context()
        client.cycles.delete(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

    @mcp.tool()
    def manage_cycle_work_items(
        project_id: str,
        cycle_id: str,
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
    ) -> None:
        """
        Add or remove work items on a cycle in a single call.

        At least one of add_ids or remove_ids must be provided.

        Args:
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            add_ids: UUIDs of work items to add to the cycle
            remove_ids: UUIDs of work items to remove from the cycle
        """
        if not add_ids and not remove_ids:
            raise ValueError("At least one of add_ids or remove_ids must be provided.")
        client, workspace_slug = get_plane_client_context()
        if add_ids:
            client.cycles.add_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                issue_ids=add_ids,
            )
        if remove_ids:
            for work_item_id in remove_ids:
                client.cycles.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    work_item_id=work_item_id,
                )

    @mcp.tool()
    def list_cycle_work_items(
        project_id: str,
        cycle_id: str,
        pql: Annotated[str | None, Field(description=PQL_FIELD_HINT)] = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """
        List work items in a cycle with optional PQL filtering.

        Args:
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            pql: PQL filter expression. See field description for syntax.
                Omit to list all items in the cycle.
            order_by: Field to sort by; prefix with `-` for descending.
            per_page: Results per page, 1-100 (default 25).
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
            response: PaginatedCycleWorkItemResponse = client.cycles.list_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                params=params,
            )
        except HttpError as e:
            if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
                logger.warning("list_cycle_work_items: invalid PQL %r → %s", pql, e.response)
                return {
                    "error": e.response["pql"],
                    "failed_pql": pql,
                    "pql_reference": PQL_FULL_REFERENCE,
                    "hint": "The PQL above failed. Fix it using the reference and retry list_cycle_work_items.",
                }
            raise
        return {
            "results": [
                item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])
            ],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
        }

    @mcp.tool()
    def transfer_cycle_work_items(
        project_id: str,
        cycle_id: str,
        new_cycle_id: str,
    ) -> None:
        """
        Transfer work items from one cycle to another.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            cycle_id: UUID of the source cycle
            new_cycle_id: UUID of the target cycle to transfer issues to
        """
        client, workspace_slug = get_plane_client_context()

        data = TransferCycleWorkItemsRequest(new_cycle_id=new_cycle_id)

        client.cycles.transfer_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=data,
        )

    @mcp.tool()
    def manage_cycle_archive(project_id: str, cycle_id: str, archive: bool) -> bool:
        """
        Archive or unarchive a cycle.

        Plane requires the cycle end_date to be in the past before archiving.
        When archive=True, this tool automatically sets end_date to today if
        the cycle is still active (end_date is missing or in the future),
        then archives it.

        Args:
            project_id: UUID of the project
            cycle_id: UUID of the cycle
            archive: True to archive the cycle, False to unarchive it

        Returns:
            True if the operation completed successfully
        """
        client, workspace_slug = get_plane_client_context()
        if not archive:
            return client.cycles.unarchive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

        today = date.today().isoformat()
        cycle = client.cycles.retrieve(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
        end_date = cycle.end_date if hasattr(cycle, "end_date") else None
        if not end_date or end_date > today:
            client.cycles.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                data=UpdateCycle(end_date=today),
            )

        return client.cycles.archive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

    @mcp.tool()
    def complete_cycle(project_id: str, cycle_id: str) -> Cycle:
        """
        Complete (close) a cycle by setting its end date to today.

        Plane has no explicit "complete" action — a cycle is considered complete
        when its end_date is in the past. This tool sets end_date to today,
        effectively closing the cycle.

        Args:
            project_id: UUID of the project
            cycle_id: UUID of the cycle to complete

        Returns:
            Updated Cycle object
        """
        client, workspace_slug = get_plane_client_context()
        today = date.today().isoformat()
        return client.cycles.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=UpdateCycle(end_date=today),
        )
