"""Milestone-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.milestones import (
    CreateMilestone,
    Milestone,
    MilestoneWorkItem,
    PaginatedMilestoneResponse,
    PaginatedMilestoneWorkItemResponse,
    UpdateMilestone,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.utils import deserialize_list_param


def register_milestone_tools(mcp: FastMCP) -> None:
    """Register all milestone-related tools with the MCP server."""

    @mcp.tool()
    def list_milestones(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Milestone]:
        """
        List all milestones in a project.

        Args:
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of Milestone objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedMilestoneResponse = client.milestones.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_milestone(
        project_id: str,
        title: str,
        target_date: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Milestone:
        """
        Create a new milestone.

        Args:
            project_id: UUID of the project
            title: Milestone title
            target_date: Target date for the milestone (ISO 8601 format)
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created Milestone object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateMilestone(
            title=title,
            target_date=target_date,
            external_source=external_source,
            external_id=external_id,
        )

        return client.milestones.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def retrieve_milestone(project_id: str, milestone_id: str) -> Milestone:
        """
        Retrieve a milestone by ID.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone

        Returns:
            Milestone object
        """
        client, workspace_slug = get_plane_client_context()
        return client.milestones.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, milestone_id=milestone_id
        )

    @mcp.tool()
    def update_milestone(
        project_id: str,
        milestone_id: str,
        title: str | None = None,
        target_date: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Milestone:
        """
        Update a milestone by ID.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone
            title: Milestone title
            target_date: Target date for the milestone (ISO 8601 format)
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated Milestone object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateMilestone(
            title=title,
            target_date=target_date,
            external_source=external_source,
            external_id=external_id,
        )

        return client.milestones.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            milestone_id=milestone_id,
            data=data,
        )

    @mcp.tool()
    def delete_milestone(project_id: str, milestone_id: str) -> None:
        """
        Delete a milestone by ID.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone
        """
        client, workspace_slug = get_plane_client_context()
        client.milestones.delete(
            workspace_slug=workspace_slug, project_id=project_id, milestone_id=milestone_id
        )

    @mcp.tool()
    def add_work_items_to_milestone(
        project_id: str,
        milestone_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Add work items to a milestone.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone
            issue_ids: List of work item IDs to add to the milestone
        """
        client, workspace_slug = get_plane_client_context()

        # Deserialize list params that FastMCP may have JSON-encoded as strings
        issue_ids = deserialize_list_param(issue_ids)

        client.milestones.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            milestone_id=milestone_id,
            issue_ids=issue_ids,
        )

    @mcp.tool()
    def remove_work_items_from_milestone(
        project_id: str,
        milestone_id: str,
        issue_ids: list[str],
    ) -> None:
        """
        Remove work items from a milestone.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone
            issue_ids: List of work item IDs to remove from the milestone
        """
        client, workspace_slug = get_plane_client_context()

        # Deserialize list params that FastMCP may have JSON-encoded as strings
        issue_ids = deserialize_list_param(issue_ids)

        client.milestones.remove_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            milestone_id=milestone_id,
            issue_ids=issue_ids,
        )

    @mcp.tool()
    def list_milestone_work_items(
        project_id: str,
        milestone_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[MilestoneWorkItem]:
        """
        List work items in a milestone.

        Args:
            project_id: UUID of the project
            milestone_id: UUID of the milestone
            params: Optional query parameters as a dictionary

        Returns:
            List of MilestoneWorkItem objects in the milestone
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedMilestoneWorkItemResponse = client.milestones.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            milestone_id=milestone_id,
            params=params,
        )
        return response.results
