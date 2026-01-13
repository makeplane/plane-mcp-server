"""Project-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.projects import (
    CreateProject,
    PaginatedProjectResponse,
    Project,
    ProjectFeature,
    ProjectWorklogSummary,
    UpdateProject,
)
from plane.models.query_params import PaginatedQueryParams
from plane.models.users import UserLite

from plane_mcp.client import get_plane_client_context


def register_project_tools(mcp: FastMCP) -> None:
    """Register all project-related tools with the MCP server."""

    @mcp.tool()
    def list_projects(
        cursor: str | None = None,
        per_page: int | None = None,
        expand: str | None = None,
        fields: str | None = None,
        order_by: str | None = None,
    ) -> list[Project]:
        """
        List all projects in a workspace.

        Args:
            workspace_slug: The workspace slug identifier
            cursor: Pagination cursor for getting next set of results
            per_page: Number of results per page (1-100)
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response
            order_by: Field to order results by. Prefix with '-' for descending order

        Returns:
            List of Project objects
        """
        client, workspace_slug = get_plane_client_context()

        params = PaginatedQueryParams(
            cursor=cursor,
            per_page=per_page,
            expand=expand,
            fields=fields,
            order_by=order_by,
        )

        response: PaginatedProjectResponse = client.projects.list(
            workspace_slug=workspace_slug,
            params=params,
        )

        return response.results

    @mcp.tool()
    def create_project(
        name: str,
        identifier: str,
        description: str | None = None,
        project_lead: str | None = None,
        default_assignee: str | None = None,
        emoji: str | None = None,
        cover_image: str | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        is_issue_type_enabled: bool | None = None,
    ) -> Project:
        """
        Create a new project.

        Args:
            workspace_slug: The workspace slug identifier
            name: Project name
            identifier: Project identifier (e.g., "MP" for "My Project")
            description: Project description
            project_lead: UUID of the project lead user
            default_assignee: UUID of the default assignee user
            emoji: Emoji for the project
            cover_image: Cover image URL or asset ID
            module_view: Enable module view
            cycle_view: Enable cycle view
            issue_views_view: Enable issue views view
            page_view: Enable page view
            intake_view: Enable intake view
            guest_view_all_features: Allow guests to view all features
            archive_in: Days until auto-archive
            close_in: Days until auto-close
            timezone: Project timezone
            external_source: External system source name
            external_id: External system identifier
            is_issue_type_enabled: Enable issue types

        Returns:
            Created Project object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateProject(
            name=name,
            identifier=identifier,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            emoji=emoji,
            cover_image=cover_image,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=timezone,
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
        )

        return client.projects.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_project(project_id: str) -> Project:
        """
        Retrieve a project by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project

        Returns:
            Project object
        """
        client, workspace_slug = get_plane_client_context()
        return client.projects.retrieve(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def update_project(
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        project_lead: str | None = None,
        default_assignee: str | None = None,
        identifier: str | None = None,
        emoji: str | None = None,
        cover_image: str | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        is_issue_type_enabled: bool | None = None,
        is_time_tracking_enabled: bool | None = None,
        default_state: str | None = None,
        estimate: str | None = None,
    ) -> Project:
        """
        Update a project by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Project name
            description: Project description
            project_lead: UUID of the project lead user
            default_assignee: UUID of the default assignee user
            identifier: Project identifier
            emoji: Emoji for the project
            cover_image: Cover image URL or asset ID
            module_view: Enable module view
            cycle_view: Enable cycle view
            issue_views_view: Enable issue views view
            page_view: Enable page view
            intake_view: Enable intake view
            guest_view_all_features: Allow guests to view all features
            archive_in: Days until auto-archive
            close_in: Days until auto-close
            timezone: Project timezone
            external_source: External system source name
            external_id: External system identifier
            is_issue_type_enabled: Enable issue types
            is_time_tracking_enabled: Enable time tracking
            default_state: UUID of the default state
            estimate: Estimate configuration

        Returns:
            Updated Project object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateProject(
            name=name,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            identifier=identifier,
            emoji=emoji,
            cover_image=cover_image,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=timezone,
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
            is_time_tracking_enabled=is_time_tracking_enabled,
            default_state=default_state,
            estimate=estimate,
        )

        return client.projects.update(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def delete_project(project_id: str) -> None:
        """
        Delete a project by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
        """
        client, workspace_slug = get_plane_client_context()
        client.projects.delete(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def get_project_worklog_summary(project_id: str) -> list[ProjectWorklogSummary]:
        """
        Get work log summary for a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project

        Returns:
            List of ProjectWorklogSummary objects containing work item IDs and durations
        """
        client, workspace_slug = get_plane_client_context()
        return client.projects.get_worklog_summary(
            workspace_slug=workspace_slug, project_id=project_id
        )

    @mcp.tool()
    def get_project_members(
        project_id: str, params: dict[str, Any] | None = None
    ) -> list[UserLite]:
        """
        Get all members of a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of UserLite objects representing project members
        """
        client, workspace_slug = get_plane_client_context()
        return client.projects.get_members(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )

    @mcp.tool()
    def get_project_features(project_id: str) -> ProjectFeature:
        """
        Get features of a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project

        Returns:
            ProjectFeature object containing enabled/disabled features
        """
        client, workspace_slug = get_plane_client_context()
        return client.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def update_project_features(
        project_id: str,
        epics: bool | None = None,
        modules: bool | None = None,
        cycles: bool | None = None,
        views: bool | None = None,
        pages: bool | None = None,
        intakes: bool | None = None,
        work_item_types: bool | None = None,
    ) -> ProjectFeature:
        """
        Update features of a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            epics: Enable/disable epics feature
            modules: Enable/disable modules feature
            cycles: Enable/disable cycles feature
            views: Enable/disable views feature
            pages: Enable/disable pages feature
            intakes: Enable/disable intakes feature
            work_item_types: Enable/disable work item types feature

        Returns:
            Updated ProjectFeature object
        """
        client, workspace_slug = get_plane_client_context()

        data = ProjectFeature(
            epics=epics,
            modules=modules,
            cycles=cycles,
            views=views,
            pages=pages,
            intakes=intakes,
            work_item_types=work_item_types,
        )

        return client.projects.update_features(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )
