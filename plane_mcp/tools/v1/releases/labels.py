"""Release label tools for Plane MCP Server.

Two layers: the workspace's palette of release labels (create/update/delete), and
which of those labels are attached to a given release (manage_release_labels).
"""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.releases import (
    AddReleaseItemLabel,
    CreateReleaseLabel,
    PaginatedReleaseLabelResponse,
    ReleaseLabel,
    RemoveReleaseItemLabel,
    UpdateReleaseLabel,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v1.releases._helpers import page_params


def register_release_label_tools(mcp: FastMCP) -> None:
    """Register the release label tools with the MCP server."""

    @mcp.tool()
    def list_release_labels(
        release_id: str | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseLabelResponse:
        """
        List release labels (paginated).

        With release_id, lists the labels attached to that release. Without it,
        lists the workspace's whole palette of release labels.

        Args:
            release_id: UUID of a release to scope to; omit for the workspace palette.
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        params = page_params(cursor, per_page)
        if release_id:
            return client.releases.item_labels.list(workspace_slug=workspace_slug, release_id=release_id, params=params)
        return client.releases.labels.list(workspace_slug=workspace_slug, params=params)

    @mcp.tool()
    def create_release_label(
        name: str,
        color: str | None = None,
        sort_order: int | None = None,
    ) -> ReleaseLabel:
        """
        Create a release label in the workspace palette. Name must be unique.

        This defines a label; attach it to a release with manage_release_labels.

        Args:
            name: Label name
            color: Hex color, e.g. "#4E5355"
            sort_order: Position in the palette

        Returns:
            The created ReleaseLabel
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateReleaseLabel(name=name, color=color, sort_order=sort_order)
        return client.releases.labels.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def update_release_label(
        label_id: str,
        name: str | None = None,
        color: str | None = None,
        sort_order: int | None = None,
    ) -> ReleaseLabel:
        """
        Update a release label in the palette. Only the fields you pass are changed.

        Args:
            label_id: UUID of the release label
            name: Label name
            color: Hex color, e.g. "#4E5355"
            sort_order: Position in the palette

        Returns:
            The updated ReleaseLabel
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateReleaseLabel(name=name, color=color, sort_order=sort_order)
        return client.releases.labels.update(workspace_slug=workspace_slug, label_id=label_id, data=data)

    @mcp.tool()
    def delete_release_label(label_id: str) -> None:
        """
        Delete a release label from the workspace palette, detaching it everywhere.

        To only remove a label from one release, use manage_release_labels with
        action="detach".

        Args:
            label_id: UUID of the release label
        """
        client, workspace_slug = get_plane_client_context()
        client.releases.labels.delete(workspace_slug=workspace_slug, label_id=label_id)

    @mcp.tool()
    def manage_release_labels(
        release_id: str,
        action: Literal["attach", "detach"],
        label_ids: list[str],
    ) -> list[ReleaseLabel]:
        """
        Attach or detach existing palette labels on a release. Use list_release_labels
        with release_id to read.

        Args:
            release_id: UUID of the release
            action: "attach" to add the labels, "detach" to remove them
            label_ids: UUIDs of palette labels to attach/detach

        Returns:
            The release's attached labels after the operation
        """
        client, workspace_slug = get_plane_client_context()
        if not label_ids:
            raise ToolError("label_ids must not be empty.")

        item_labels = client.releases.item_labels
        if action == "attach":
            item_labels.create(
                workspace_slug=workspace_slug,
                release_id=release_id,
                data=AddReleaseItemLabel(label_ids=label_ids),
            )
        else:
            item_labels.delete(
                workspace_slug=workspace_slug,
                release_id=release_id,
                data=RemoveReleaseItemLabel(label_ids=label_ids),
            )

        return item_labels.list(workspace_slug=workspace_slug, release_id=release_id).results
