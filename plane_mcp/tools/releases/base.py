"""Release tools for Plane MCP Server."""

from typing import Literal

from fastmcp import FastMCP
from plane.models.releases import (
    CreateRelease,
    PaginatedReleaseResponse,
    Release,
    UpdateRelease,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.releases._helpers import page_params

_STATUS = Literal["unreleased", "released", "cancelled"]


def register_release_base_tools(mcp: FastMCP) -> None:
    """Register the release CRUD tools with the MCP server."""

    @mcp.tool()
    def list_releases(
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedReleaseResponse:
        """
        List releases in the workspace (paginated).

        Args:
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (default 20).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.list(workspace_slug=workspace_slug, params=page_params(cursor, per_page))

    @mcp.tool()
    def create_release(
        name: str,
        description_html: str | None = None,
        status: _STATUS | None = None,
        target_date: str | None = None,
        release_date: str | None = None,
        tag_id: str | None = None,
        lead_id: str | None = None,
        is_prerelease: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Release:
        """
        Create a release in the workspace. Name must be unique in the workspace.

        Args:
            name: Release name
            description_html: HTML body of the release notes
            status: unreleased (default) | released | cancelled
            target_date: Planned date, YYYY-MM-DD
            release_date: Actual release date, YYYY-MM-DD
            tag_id: UUID of a release tag (version marker) to attach
            lead_id: UUID of the user leading the release
            is_prerelease: Whether this is a pre-release
            external_source: External system this release came from, e.g. "github"
            external_id: The release's ID in that external system

        Returns:
            The created Release. Its description comes back as a nested object.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateRelease(
            name=name,
            description_html=description_html,
            status=status,
            target_date=target_date,
            release_date=release_date,
            tag=tag_id,
            lead=lead_id,
            is_prerelease=is_prerelease,
            external_source=external_source,
            external_id=external_id,
        )
        return client.releases.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_release(release_id: str) -> Release:
        """
        Retrieve a release by ID.

        Args:
            release_id: UUID of the release

        Returns:
            The Release
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.retrieve(workspace_slug=workspace_slug, release_id=release_id)

    @mcp.tool()
    def update_release(
        release_id: str,
        name: str | None = None,
        description_html: str | None = None,
        status: _STATUS | None = None,
        target_date: str | None = None,
        release_date: str | None = None,
        tag_id: str | None = None,
        lead_id: str | None = None,
        is_prerelease: bool | None = None,
    ) -> Release:
        """
        Update a release by ID. Only the fields you pass are changed.

        Args:
            release_id: UUID of the release
            name: Release name
            description_html: HTML body of the release notes
            status: unreleased | released | cancelled
            target_date: Planned date, YYYY-MM-DD
            release_date: Actual release date, YYYY-MM-DD
            tag_id: UUID of a release tag to attach
            lead_id: UUID of the user leading the release
            is_prerelease: Whether this is a pre-release

        Returns:
            The updated Release
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateRelease(
            name=name,
            description_html=description_html,
            status=status,
            target_date=target_date,
            release_date=release_date,
            tag=tag_id,
            lead=lead_id,
            is_prerelease=is_prerelease,
        )
        return client.releases.update(workspace_slug=workspace_slug, release_id=release_id, data=data)

    @mcp.tool()
    def delete_release(release_id: str) -> None:
        """
        Delete a release by ID.

        Args:
            release_id: UUID of the release
        """
        client, workspace_slug = get_plane_client_context()
        client.releases.delete(workspace_slug=workspace_slug, release_id=release_id)
