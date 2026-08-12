"""Workspace-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.projects import ProjectFeature
from plane.models.query_params import MemberListQueryParams
from plane.models.workspaces import PaginatedWorkspaceMemberResponse, WorkspaceFeature

from plane_mcp.client import get_plane_client_context


def register_workspace_tools(mcp: FastMCP) -> None:
    """Register all workspace-related tools with the MCP server."""

    @mcp.tool()
    def get_workspace_members(
        first_name: str | None = None,
        last_name: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        role_slug: str | None = None,
        is_active: bool | None = None,
        is_bot: bool | None = None,
        cursor: str | None = None,
        per_page: int | None = 100,
        order_by: str | None = None,
    ) -> PaginatedWorkspaceMemberResponse:
        """
        List members of the current workspace (filterable, paginated).

        Optional filters first_name/last_name/email/display_name (case-insensitive
        contains), role_slug (exact), is_active, is_bot — combined with AND.

        Args:
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (1-1000, default 100).
            order_by: Sort field; prefix '-' for descending.

        Returns:
            Paginated envelope: results (members incl. role, role_slug,
            is_active, is_bot) + total_count, next_cursor, next_page_results.
        """
        client, workspace_slug = get_plane_client_context()
        params = MemberListQueryParams(
            first_name=first_name,
            last_name=last_name,
            email=email,
            display_name=display_name,
            role_slug=role_slug,
            is_active=is_active,
            is_bot=is_bot,
            cursor=cursor,
            per_page=per_page,
            order_by=order_by,
        )
        return client.workspaces.get_members_lite(workspace_slug=workspace_slug, params=params)

    @mcp.tool()
    def get_features(project_id: str | None = None) -> WorkspaceFeature | ProjectFeature:
        """
        Get feature flags.

        Returns a project's features if project_id is given, otherwise the
        workspace's features.

        Args:
            project_id: UUID of the project. Omit for workspace features.

        Returns:
            ProjectFeature when project_id is given, otherwise WorkspaceFeature.
        """
        client, workspace_slug = get_plane_client_context()
        if project_id is not None:
            return client.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)
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

        return client.workspaces.update_features(workspace_slug=workspace_slug, data=data)
