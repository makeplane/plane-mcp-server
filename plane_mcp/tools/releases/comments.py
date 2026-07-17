"""Release comment tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.releases import (
    CreateReleaseComment,
    PaginatedReleaseCommentResponse,
    ReleaseComment,
    UpdateReleaseComment,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.releases._helpers import page_params


def register_release_comment_tools(mcp: FastMCP) -> None:
    """Register the release comment tools with the MCP server."""

    @mcp.tool()
    def list_release_comments(
        release_id: str,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseCommentResponse:
        """
        List the comments on a release (paginated).

        Args:
            release_id: UUID of the release
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.comments.list(
            workspace_slug=workspace_slug, release_id=release_id, params=page_params(cursor, per_page)
        )

    @mcp.tool()
    def create_release_comment(
        release_id: str,
        comment_html: str,
        parent_id: str | None = None,
    ) -> ReleaseComment:
        """
        Add a comment to a release.

        Args:
            release_id: UUID of the release
            comment_html: HTML body of the comment
            parent_id: UUID of the comment this replies to, for a threaded reply

        Returns:
            The created ReleaseComment. Its body comes back as a nested object.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateReleaseComment(comment_html=comment_html, parent=parent_id)
        return client.releases.comments.create(workspace_slug=workspace_slug, release_id=release_id, data=data)

    @mcp.tool()
    def retrieve_release_comment(release_id: str, comment_id: str) -> ReleaseComment:
        """
        Retrieve a release comment by ID.

        Args:
            release_id: UUID of the release
            comment_id: UUID of the comment

        Returns:
            The ReleaseComment
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.comments.retrieve(
            workspace_slug=workspace_slug, release_id=release_id, comment_id=comment_id
        )

    @mcp.tool()
    def update_release_comment(
        release_id: str,
        comment_id: str,
        comment_html: str | None = None,
        is_resolved: bool | None = None,
    ) -> ReleaseComment:
        """
        Update a release comment by ID. Only the fields you pass are changed.

        Args:
            release_id: UUID of the release
            comment_id: UUID of the comment
            comment_html: HTML body of the comment
            is_resolved: Whether the comment thread is resolved

        Returns:
            The updated ReleaseComment
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateReleaseComment(comment_html=comment_html, is_resolved=is_resolved)
        return client.releases.comments.update(
            workspace_slug=workspace_slug, release_id=release_id, comment_id=comment_id, data=data
        )

    @mcp.tool()
    def delete_release_comment(release_id: str, comment_id: str) -> None:
        """
        Delete a release comment by ID.

        Args:
            release_id: UUID of the release
            comment_id: UUID of the comment
        """
        client, workspace_slug = get_plane_client_context()
        client.releases.comments.delete(workspace_slug=workspace_slug, release_id=release_id, comment_id=comment_id)
