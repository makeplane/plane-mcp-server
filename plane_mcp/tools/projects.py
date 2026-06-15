"""Project-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import TimezoneEnum
from plane.models.estimates import (
    CreateEstimate,
    CreateEstimatePoint,
    Estimate,
    EstimatePoint,
    UpdateEstimate,
    UpdateEstimatePoint,
)
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

        # Validate timezone against allowed literal values
        validated_timezone: TimezoneEnum | None = (
            timezone if timezone in get_args(TimezoneEnum) else None  # type: ignore[assignment]
        )

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
            timezone=validated_timezone,
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
        network: int | None = None,
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
            network: Project visibility (0=secret, 2=public)
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
        if network is not None and network not in {0, 2}:
            raise ValueError("network must be 0 (secret) or 2 (public)")

        client, workspace_slug = get_plane_client_context()

        # Validate timezone against allowed literal values
        validated_timezone: TimezoneEnum | None = (
            timezone if timezone in get_args(TimezoneEnum) else None  # type: ignore[assignment]
        )

        data = UpdateProject(
            name=name,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            identifier=identifier,
            emoji=emoji,
            cover_image=cover_image,
            network=network,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=validated_timezone,
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
            is_time_tracking_enabled=is_time_tracking_enabled,
            default_state=default_state,
            estimate=estimate,
        )

        return client.projects.update(workspace_slug=workspace_slug, project_id=project_id, data=data)

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
    def manage_project_archive(project_id: str, archive: bool) -> None:
        """
        Archive or unarchive a project.

        Archived projects are hidden from active project lists but not deleted.
        All work items, cycles, and modules are preserved.

        Args:
            project_id: UUID of the project
            archive: True to archive the project, False to unarchive it
        """
        client, workspace_slug = get_plane_client_context()
        if archive:
            client.projects.archive(workspace_slug=workspace_slug, project_id=project_id)
        else:
            client.projects.unarchive(workspace_slug=workspace_slug, project_id=project_id)

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
        return client.projects.get_worklog_summary(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def get_project_members(project_id: str, params: dict[str, Any] | None = None) -> list[UserLite]:
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
        return client.projects.get_members(workspace_slug=workspace_slug, project_id=project_id, params=params)

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
            modules=modules,
            cycles=cycles,
            views=views,
            pages=pages,
            intakes=intakes,
            work_item_types=work_item_types,
        )

        return client.projects.update_features(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def get_project_estimate(project_id: str) -> Estimate:
        """
        Get the estimate configuration for a project.

        Returns the active estimate system including its ID, which is required
        to call list_project_estimate_points.

        Args:
            project_id: UUID of the project

        Returns:
            Estimate object with id, name, and type fields
        """
        client, workspace_slug = get_plane_client_context()
        return client.estimates.retrieve(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def list_project_estimate_points(project_id: str, estimate_id: str) -> list[EstimatePoint]:
        """
        List all valid estimate points for a project.

        Use this to discover the available estimate point UUIDs before calling
        update_work_item with an estimate_point value. Each EstimatePoint has
        an id (UUID to pass to update_work_item) and a value (display label
        such as "1", "2", "3", "5", "8" or "XS", "S", "M", "L", "XL").

        Workflow:
            1. Call get_project_estimate to get the estimate_id
            2. Call list_project_estimate_points with that estimate_id
            3. Pick the EstimatePoint whose value matches the user's intent
            4. Pass that EstimatePoint.id to update_work_item(estimate_point=...)

        Args:
            project_id: UUID of the project
            estimate_id: UUID of the estimate (from get_project_estimate)

        Returns:
            List of EstimatePoint objects, each with id and value fields
        """
        client, workspace_slug = get_plane_client_context()
        return client.estimates.list_points(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
        )

    @mcp.tool()
    def create_project_estimate(
        project_id: str,
        name: str,
        type: str | None = None,
        description: str | None = None,
        last_used: bool = True,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> Estimate:
        """
        Create a new estimate for a project.

        Args:
            project_id: UUID of the project
            name: Name of the estimate (e.g., "Story Points", "T-Shirt Sizes")
            type: Estimate type — "categories", "points", or "time"
            description: Optional description
            last_used: Whether this becomes the active estimate (default True)
            external_id: External system identifier
            external_source: External system source name

        Returns:
            Created Estimate object
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateEstimate(
            name=name,
            type=type,
            description=description,
            last_used=last_used,
            external_id=external_id,
            external_source=external_source,
        )
        return client.estimates.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_project_estimate(
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> Estimate:
        """
        Update the estimate for a project.

        Args:
            project_id: UUID of the project
            name: New name for the estimate
            description: New description
            external_id: External system identifier
            external_source: External system source name

        Returns:
            Updated Estimate object
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateEstimate(
            name=name,
            description=description,
            external_id=external_id,
            external_source=external_source,
        )
        return client.estimates.update(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def delete_project_estimate(project_id: str) -> None:
        """
        Delete the estimate for a project.

        Args:
            project_id: UUID of the project
        """
        client, workspace_slug = get_plane_client_context()
        client.estimates.delete(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def link_estimate_to_project(project_id: str, estimate_id: str) -> Project:
        """
        Link an estimate to a project, making it the active estimate system.

        Args:
            project_id: UUID of the project
            estimate_id: UUID of the estimate to activate

        Returns:
            Updated Project object
        """
        client, workspace_slug = get_plane_client_context()
        return client.estimates.link_to_project(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
        )

    @mcp.tool()
    def create_project_estimate_points(
        project_id: str,
        estimate_id: str,
        points: list[dict],
    ) -> list[EstimatePoint]:
        """
        Create estimate points for a project estimate.

        Each point dict may have: value (required, max 20 chars), key (int),
        description, external_id, external_source.

        Example:
            points=[
                {"value": "1", "key": 0},
                {"value": "2", "key": 1},
                {"value": "3", "key": 2},
                {"value": "5", "key": 3},
                {"value": "8", "key": 4},
            ]

        Args:
            project_id: UUID of the project
            estimate_id: UUID of the estimate
            points: List of point definitions

        Returns:
            List of created EstimatePoint objects
        """
        client, workspace_slug = get_plane_client_context()
        data = [CreateEstimatePoint(**p) for p in points]
        return client.estimates.create_points(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            data=data,
        )

    @mcp.tool()
    def update_project_estimate_point(
        project_id: str,
        estimate_id: str,
        estimate_point_id: str,
        value: str | None = None,
        key: int | None = None,
        description: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> EstimatePoint:
        """
        Update a single estimate point.

        Args:
            project_id: UUID of the project
            estimate_id: UUID of the estimate
            estimate_point_id: UUID of the estimate point to update
            value: New display value (max 20 chars, e.g. "XL", "13")
            key: New sort key (integer)
            description: New description
            external_id: External system identifier
            external_source: External system source name

        Returns:
            Updated EstimatePoint object
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateEstimatePoint(
            value=value,
            key=key,
            description=description,
            external_id=external_id,
            external_source=external_source,
        )
        return client.estimates.update_point(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            estimate_point_id=estimate_point_id,
            data=data,
        )

    @mcp.tool()
    def delete_project_estimate_point(
        project_id: str,
        estimate_id: str,
        estimate_point_id: str,
    ) -> None:
        """
        Delete a single estimate point.

        Args:
            project_id: UUID of the project
            estimate_id: UUID of the estimate
            estimate_point_id: UUID of the estimate point to delete
        """
        client, workspace_slug = get_plane_client_context()
        client.estimates.delete_point(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            estimate_point_id=estimate_point_id,
        )
