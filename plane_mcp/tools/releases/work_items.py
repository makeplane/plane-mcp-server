"""Release work item tools for Plane MCP Server."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.releases import (
    AddReleaseWorkItems,
    PaginatedReleaseWorkItemResponse,
    ReleaseWorkItem,
    RemoveReleaseWorkItems,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.releases._helpers import page_params


def register_release_work_item_tools(mcp: FastMCP) -> None:
    """Register the release work item tools with the MCP server."""

    @mcp.tool()
    def list_release_work_items(
        release_id: str,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseWorkItemResponse:
        """
        List the work items linked to a release (paginated).

        Args:
            release_id: UUID of the release
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.work_items.list(
            workspace_slug=workspace_slug, release_id=release_id, params=page_params(cursor, per_page)
        )

    @mcp.tool()
    def manage_release_work_items(
        release_id: str,
        action: Literal["add", "remove"],
        work_item_ids: list[str],
    ) -> list[ReleaseWorkItem]:
        """
        Add or remove work items on a release. Use list_release_work_items to read.

        Args:
            release_id: UUID of the release
            action: "add" to link the work items, "remove" to unlink them
            work_item_ids: Work item UUIDs to add/remove

        Returns:
            The release's linked work items after the operation
        """
        client, workspace_slug = get_plane_client_context()
        if not work_item_ids:
            raise ToolError("work_item_ids must not be empty.")

        work_items = client.releases.work_items
        if action == "add":
            work_items.create(
                workspace_slug=workspace_slug,
                release_id=release_id,
                data=AddReleaseWorkItems(work_item_ids=work_item_ids),
            )
        else:
            work_items.delete(
                workspace_slug=workspace_slug,
                release_id=release_id,
                data=RemoveReleaseWorkItems(work_item_ids=work_item_ids),
            )

        return work_items.list(workspace_slug=workspace_slug, release_id=release_id).results
