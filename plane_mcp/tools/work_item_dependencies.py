"""Work item dependency tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.work_items import (
    CreateWorkItemDependency,
    WorkItemDependencyResponse,
    WorkItemWithRelationType,
)

from plane_mcp.client import get_plane_client_context

_DEPENDENCY_TYPES = (
    "blocking",
    "blocked_by",
    "start_before",
    "start_after",
    "finish_before",
    "finish_after",
)


def register_work_item_dependency_tools(mcp: FastMCP) -> None:
    """Register work item dependency tools with the MCP server."""

    @mcp.tool()
    def list_work_item_dependencies(
        project_id: str,
        work_item_id: str,
    ) -> WorkItemDependencyResponse:
        """List dependency relations for a work item, grouped by direction.

        Returns six groups: blocking, blocked_by, start_before, start_after,
        finish_before, finish_after. Each group contains full work item objects
        with a relation_type field indicating the direction.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the work item.

        Returns:
            WorkItemDependencyResponse with a list of work items per dependency direction.
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.dependencies.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def create_work_item_dependency(
        project_id: str,
        work_item_id: str,
        relation_type: str,
        work_item_ids: list[str],
    ) -> list[WorkItemWithRelationType]:
        """Create one or more dependency relations for a work item.

        relation_type controls directionality from this work item's perspective.
        Allowed values: blocking, blocked_by, start_before, start_after,
        finish_before, finish_after.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            relation_type: Dependency direction. One of: blocking, blocked_by,
                start_before, start_after, finish_before, finish_after.
            work_item_ids: UUIDs of target work items to create dependencies with.

        Returns:
            List of created WorkItemWithRelationType objects.
        """
        if relation_type not in _DEPENDENCY_TYPES:
            raise ValueError(
                f"Invalid relation_type '{relation_type}'. Must be one of: {_DEPENDENCY_TYPES}"
            )
        client, workspace_slug = get_plane_client_context()
        return client.work_items.dependencies.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemDependency(
                relation_type=relation_type,  # type: ignore[arg-type]
                work_item_ids=work_item_ids,
            ),
        )

    @mcp.tool()
    def remove_work_item_dependency(
        project_id: str,
        work_item_id: str,
        related_work_item_id: str,
    ) -> None:
        """Remove a dependency relation between two work items.

        Removes any dependency (in either direction) between the source and target
        work item. The relation_type does not need to be specified.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            related_work_item_id: UUID of the related work item to remove the dependency with.
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.dependencies.remove(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            related_work_item_id=related_work_item_id,
        )
