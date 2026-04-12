"""Project-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import TimezoneEnum
from plane.models.projects import (
    CreateProject,
    Project,
    ProjectFeature,
    ProjectWorklogSummary,
    UpdateProject,
)

from plane_mcp.client import get_plane_client_context


def register_project_tools(mcp: FastMCP) -> None:
    """
    Create a new project in the current workspace.
    
    Parameters:
        name: Project name.
        identifier: Project identifier (short string like "MP").
        description: Project description.
        project_lead: UUID of the project lead user.
        default_assignee: UUID of the default assignee user.
        emoji: Emoji for the project.
        cover_image: Cover image URL or asset ID.
        module_view: Enable module view.
        cycle_view: Enable cycle view.
        issue_views_view: Enable issue views.
        page_view: Enable page view.
        intake_view: Enable intake view.
        guest_view_all_features: Allow guests to view all features.
        archive_in: Days until auto-archive.
        close_in: Days until auto-close.
        timezone: Timezone string; must match an allowed TimezoneEnum literal or it will be ignored.
        external_source: External system source name.
        external_id: External system identifier.
        is_issue_type_enabled: Enable issue types.
    
    Returns:
        Created Project object.
    """
    """
    Update an existing project by ID in the current workspace.
    
    Parameters:
        project_id: UUID of the project to update.
        name: New project name.
        description: New project description.
        project_lead: UUID of the project lead user.
        default_assignee: UUID of the default assignee user.
        identifier: New project identifier.
        emoji: Emoji for the project.
        cover_image: Cover image URL or asset ID.
        module_view: Enable/disable module view.
        cycle_view: Enable/disable cycle view.
        issue_views_view: Enable/disable issue views.
        page_view: Enable/disable page view.
        intake_view: Enable/disable intake view.
        guest_view_all_features: Allow guests to view all features.
        archive_in: Days until auto-archive.
        close_in: Days until auto-close.
        timezone: Timezone string; must match an allowed TimezoneEnum literal or it will be ignored.
        external_source: External system source name.
        external_id: External system identifier.
        is_issue_type_enabled: Enable issue types.
        is_time_tracking_enabled: Enable time tracking.
        default_state: UUID of the default state.
        estimate: Estimate configuration.
    
    Returns:
        Updated Project object.
    """
    """
    Delete a project by ID from the current workspace.
    
    Parameters:
        project_id: UUID of the project to delete.
    """
    """
    Retrieve a worklog summary for a project in the current workspace.
    
    Parameters:
        project_id: UUID of the project.
    
    Returns:
        List of ProjectWorklogSummary objects containing work item IDs and aggregated durations.
    """
    """
    Retrieve the feature flags for a project in the current workspace.
    
    Parameters:
        project_id: UUID of the project.
    
    Returns:
        ProjectFeature object describing enabled and disabled features for the project.
    """
    """
    Update feature flags for a project in the current workspace.
    
    Parameters:
        project_id: UUID of the project.
        epics: Enable/disable epics feature.
        modules: Enable/disable modules feature.
        cycles: Enable/disable cycles feature.
        views: Enable/disable views feature.
        pages: Enable/disable pages feature.
        intakes: Enable/disable intakes feature.
        work_item_types: Enable/disable work item types feature.
    
    Returns:
        Updated ProjectFeature object.
    """

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
        Create a new Plane project in the current workspace.
        
        Parameters:
            name: Project name.
            identifier: Project identifier (e.g., "MP" for "My Project").
            description: Project description.
            project_lead: UUID of the project lead user.
            default_assignee: UUID of the default assignee user.
            emoji: Emoji for the project.
            cover_image: Cover image URL or asset ID.
            module_view: Enable module view.
            cycle_view: Enable cycle view.
            issue_views_view: Enable issue views view.
            page_view: Enable page view.
            intake_view: Enable intake view.
            guest_view_all_features: Allow guests to view all features.
            archive_in: Number of days until the project is auto-archived.
            close_in: Number of days until the project is auto-closed.
            timezone: Timezone identifier; if the value is not one of the allowed TimezoneEnum literals, it will be treated as `None`.
            external_source: External system source name.
            external_id: External system identifier.
            is_issue_type_enabled: Enable issue types.
        
        Returns:
            Project: The created Project object.
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
        Update fields of an existing project identified by its ID.
        
        The provided `timezone` string is validated against allowed TimezoneEnum literal values; if it does not match, the timezone is cleared (set to `None`) in the update payload.
        
        Parameters:
            project_id (str): UUID of the project to update.
            timezone (str | None): Timezone identifier to set for the project; must match an allowed TimezoneEnum literal or it will be ignored.
        
        Returns:
            Project: The updated Project object returned by the API.
        """
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
    def get_project_worklog_summary(project_id: str) -> list[ProjectWorklogSummary]:
        """
        Retrieve aggregated worklog summaries for a project.
        
        Parameters:
        	project_id (str): UUID of the project to summarize worklogs for.
        
        Returns:
        	list[ProjectWorklogSummary]: A list of ProjectWorklogSummary objects, each summarizing durations and associated work item identifiers.
        """
        client, workspace_slug = get_plane_client_context()
        return client.projects.get_worklog_summary(workspace_slug=workspace_slug, project_id=project_id)

    @mcp.tool()
    def get_project_features(project_id: str) -> ProjectFeature:
        """
        Get the feature settings for a project.
        
        Parameters:
            project_id (str): UUID of the project whose features will be retrieved.
        
        Returns:
            ProjectFeature: The project's feature configuration, indicating which features are enabled.
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

        return client.projects.update_features(workspace_slug=workspace_slug, project_id=project_id, data=data)
