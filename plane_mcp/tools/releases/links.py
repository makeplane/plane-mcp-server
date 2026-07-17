"""Release link tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.releases import (
    CreateReleaseLink,
    PaginatedReleaseLinkResponse,
    ReleaseLink,
    UpdateReleaseLink,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.releases._helpers import page_params


def register_release_link_tools(mcp: FastMCP) -> None:
    """Register the release link tools with the MCP server."""

    @mcp.tool()
    def list_release_links(
        release_id: str,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseLinkResponse:
        """
        List the links on a release (paginated).

        Args:
            release_id: UUID of the release
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.links.list(
            workspace_slug=workspace_slug, release_id=release_id, params=page_params(cursor, per_page)
        )

    @mcp.tool()
    def create_release_link(
        release_id: str,
        url: str,
        title: str | None = None,
    ) -> ReleaseLink:
        """
        Add a link to a release. The URL must be unique on the release.

        Args:
            release_id: UUID of the release
            url: The URL to link
            title: Display title for the link

        Returns:
            The created ReleaseLink
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateReleaseLink(url=url, title=title)
        return client.releases.links.create(workspace_slug=workspace_slug, release_id=release_id, data=data)

    @mcp.tool()
    def retrieve_release_link(release_id: str, link_id: str) -> ReleaseLink:
        """
        Retrieve a release link by ID.

        Args:
            release_id: UUID of the release
            link_id: UUID of the link

        Returns:
            The ReleaseLink
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.links.retrieve(workspace_slug=workspace_slug, release_id=release_id, link_id=link_id)

    @mcp.tool()
    def update_release_link(
        release_id: str,
        link_id: str,
        url: str | None = None,
        title: str | None = None,
    ) -> ReleaseLink:
        """
        Update a release link by ID. Only the fields you pass are changed.

        Args:
            release_id: UUID of the release
            link_id: UUID of the link
            url: The URL to link
            title: Display title for the link

        Returns:
            The updated ReleaseLink
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateReleaseLink(url=url, title=title)
        return client.releases.links.update(
            workspace_slug=workspace_slug, release_id=release_id, link_id=link_id, data=data
        )

    @mcp.tool()
    def delete_release_link(release_id: str, link_id: str) -> None:
        """
        Delete a release link by ID.

        Args:
            release_id: UUID of the release
            link_id: UUID of the link
        """
        client, workspace_slug = get_plane_client_context()
        client.releases.links.delete(workspace_slug=workspace_slug, release_id=release_id, link_id=link_id)
