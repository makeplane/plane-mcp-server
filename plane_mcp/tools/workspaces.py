"""Workspace-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.users import UserLite
from plane.models.workspaces import WorkspaceFeature

from plane_mcp.client import get_plane_client_context


def register_workspace_tools(mcp: FastMCP) -> None:
    """Register all workspace-related tools with the MCP server."""

    @mcp.tool()
    def get_workspace_members() -> list[UserLite]:
        """
        Get all members of the current workspace.

        Returns:
            List of UserLite objects representing workspace members
        """
        client, workspace_slug = get_plane_client_context()
        return client.workspaces.get_members(workspace_slug=workspace_slug)

    @mcp.tool()
    def get_workspace_features() -> WorkspaceFeature:
        """
        Get features of the current workspace.

        Returns:
            WorkspaceFeature object containing feature flags
        """
        client, workspace_slug = get_plane_client_context()
        return client.workspaces.get_features(workspace_slug=workspace_slug)

    @mcp.tool()
    def update_workspace_features(
        project_grouping: bool | None = None,
        initiatives: bool | None = None,
        teams: bool | None = None,
        customers: bool | None = None,
        wiki: bool | None = None,
        pi: bool | None = None,
    ) -> WorkspaceFeature:
        """
        Update features of the current workspace.

        Args:
            project_grouping: Enable/disable project grouping feature
            initiatives: Enable/disable initiatives feature
            teams: Enable/disable teams feature
            customers: Enable/disable customers feature
            wiki: Enable/disable wiki feature
            pi: Enable/disable PI (Program Increment) feature

        Returns:
            Updated WorkspaceFeature object
        """
        client, workspace_slug = get_plane_client_context()

        # Build data dict with only non-None values
        feature_data: dict[str, bool] = {}
        if project_grouping is not None:
            feature_data["project_grouping"] = project_grouping
        if initiatives is not None:
            feature_data["initiatives"] = initiatives
        if teams is not None:
            feature_data["teams"] = teams
        if customers is not None:
            feature_data["customers"] = customers
        if wiki is not None:
            feature_data["wiki"] = wiki
        if pi is not None:
            feature_data["pi"] = pi

        data = WorkspaceFeature(**feature_data)

        return client.workspaces.update_features(
            workspace_slug=workspace_slug, data=data
        )
