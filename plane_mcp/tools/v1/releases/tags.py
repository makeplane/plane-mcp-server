"""Release tag tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.releases import (
    CreateReleaseTag,
    PaginatedReleaseTagResponse,
    ReleaseTag,
    UpdateReleaseTag,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v1.releases._helpers import page_params


def register_release_tag_tools(mcp: FastMCP) -> None:
    """Register the release tag tools with the MCP server."""

    @mcp.tool()
    def list_release_tags(
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseTagResponse:
        """
        List release tags in the workspace (paginated).

        A release tag is a version marker (e.g. "v1.2.0") plus optional git
        metadata, not a label. Attach one to a release via its tag_id.

        Args:
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.tags.list(workspace_slug=workspace_slug, params=page_params(cursor, per_page))

    @mcp.tool()
    def create_release_tag(
        version: str,
        description: str | None = None,
        commit_hash: str | None = None,
        git_tag: str | None = None,
    ) -> ReleaseTag:
        """
        Create a release tag (version marker). Version must be unique in the workspace.

        Args:
            version: Version string, e.g. "v1.2.0"
            description: Optional notes about the version
            commit_hash: Git commit the version was cut from
            git_tag: Corresponding git tag name

        Returns:
            The created ReleaseTag
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateReleaseTag(version=version, description=description, commit_hash=commit_hash, git_tag=git_tag)
        return client.releases.tags.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_release_tag(tag_id: str) -> ReleaseTag:
        """
        Retrieve a release tag by ID.

        Args:
            tag_id: UUID of the release tag

        Returns:
            The ReleaseTag
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.tags.retrieve(workspace_slug=workspace_slug, tag_id=tag_id)

    @mcp.tool()
    def update_release_tag(
        tag_id: str,
        version: str | None = None,
        description: str | None = None,
        commit_hash: str | None = None,
        git_tag: str | None = None,
    ) -> ReleaseTag:
        """
        Update a release tag by ID. Only the fields you pass are changed.

        Args:
            tag_id: UUID of the release tag
            version: Version string, e.g. "v1.2.0"
            description: Notes about the version
            commit_hash: Git commit the version was cut from
            git_tag: Corresponding git tag name

        Returns:
            The updated ReleaseTag
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateReleaseTag(version=version, description=description, commit_hash=commit_hash, git_tag=git_tag)
        return client.releases.tags.update(workspace_slug=workspace_slug, tag_id=tag_id, data=data)

    @mcp.tool()
    def delete_release_tag(tag_id: str) -> None:
        """
        Delete a release tag by ID.

        Args:
            tag_id: UUID of the release tag
        """
        client, workspace_slug = get_plane_client_context()
        client.releases.tags.delete(workspace_slug=workspace_slug, tag_id=tag_id)
