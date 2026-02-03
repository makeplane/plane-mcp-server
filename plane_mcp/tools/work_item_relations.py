"""Work item relation-related tools for Plane MCP Server."""

from typing import get_args

from fastmcp import FastMCP
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_items import (
    CreateWorkItemRelation,
    RemoveWorkItemRelation,
    WorkItemRelationResponse,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_relation_tools(mcp: FastMCP) -> None:
    """Register all work item relation-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relations(
        project_id: str,
        work_item_id: str,
    ) -> WorkItemRelationResponse:
        """
        List relations for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item

        Returns:
            WorkItemRelationResponse containing lists of related work items by relation type:
            - blocking: Work items that are blocking this item
            - blocked_by: Work items that this item is blocked by
            - duplicate: Work items that are duplicates of this item
            - relates_to: Work items that relate to this item
            - start_after: Work items that start after this item
            - start_before: Work items that start before this item
            - finish_after: Work items that finish after this item
            - finish_before: Work items that finish before this item
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.relations.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def create_work_item_relation(
        project_id: str,
        work_item_id: str,
        relation_type: str,
        issues: list[str],
    ) -> None:
        """
        Create relations for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            relation_type: Type of relationship (blocking, blocked_by, duplicate,
                          relates_to, start_before, start_after, finish_before, finish_after)
            issues: List of work item IDs to create relations with
        """
        client, workspace_slug = get_plane_client_context()

        # Validate relation_type against allowed literal values
        if relation_type not in get_args(WorkItemRelationTypeEnum):
            raise ValueError(
                f"Invalid relation_type '{relation_type}'. "
                f"Must be one of: {get_args(WorkItemRelationTypeEnum)}"
            )
        validated_relation_type: WorkItemRelationTypeEnum = relation_type  # type: ignore[assignment]

        data = CreateWorkItemRelation(
            relation_type=validated_relation_type,
            issues=issues,
        )

        client.work_items.relations.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )

    @mcp.tool()
    def remove_work_item_relation(
        project_id: str,
        work_item_id: str,
        related_issue: str,
    ) -> None:
        """
        Remove a relation from a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            related_issue: UUID of the related work item to remove relation with
        """
        client, workspace_slug = get_plane_client_context()

        data = RemoveWorkItemRelation(related_issue=related_issue)

        client.work_items.relations.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )
