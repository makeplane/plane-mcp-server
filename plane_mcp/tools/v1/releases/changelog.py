"""Release changelog tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.releases import ReleaseChangelog, UpdateReleaseChangelog

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v1.releases._helpers import plain_text_to_prosemirror, resolve_description_html


def register_release_changelog_tools(mcp: FastMCP) -> None:
    """Register the release changelog tools with the MCP server."""

    @mcp.tool()
    def get_release_changelog(release_id: str) -> ReleaseChangelog:
        """
        Get a release's changelog. Each release has a single changelog, created
        empty on first access, so this always returns one.

        Args:
            release_id: UUID of the release

        Returns:
            The ReleaseChangelog. Its body comes back as a nested object.
        """
        client, workspace_slug = get_plane_client_context()
        return client.releases.changelog.retrieve(workspace_slug=workspace_slug, release_id=release_id)

    @mcp.tool()
    def update_release_changelog(
        release_id: str,
        description_html: str | None = None,
        description_stripped: str | None = None,
        description_json: Any | None = None,
    ) -> ReleaseChangelog:
        """
        Update a release's changelog body.

        The changelog UI renders from description_json, so plain text passed as
        description_stripped is also converted into a matching ProseMirror doc;
        otherwise the editor would show nothing.

        Args:
            release_id: UUID of the release
            description_html: HTML body of the changelog
            description_stripped: Plain text body. Convenience only — it is wrapped
                into both HTML (description_html) and a ProseMirror doc
                (description_json) so it renders in the changelog editor. Ignored if
                description_html is set.
            description_json: Optional ProseMirror doc. Pass this for rich content;
                it wins over the doc generated from description_stripped.

        Returns:
            The updated ReleaseChangelog
        """
        client, workspace_slug = get_plane_client_context()
        # The changelog editor is document-based (renders description_json), so a
        # bare plain-text write must also emit a matching doc. Explicit html/json win.
        if description_json is None and description_html is None and description_stripped is not None:
            description_json = plain_text_to_prosemirror(description_stripped)
        data = UpdateReleaseChangelog(
            description_html=resolve_description_html(description_html, description_stripped),
            description_json=description_json,
        )
        return client.releases.changelog.update(
            workspace_slug=workspace_slug, release_id=release_id, data=data
        )
