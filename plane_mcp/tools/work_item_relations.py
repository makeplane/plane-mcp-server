"""Work item relation-related tools for Plane MCP Server."""

from typing import get_args

from fastmcp import FastMCP
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_items import (
    CreateWorkItemRelation,
    RemoveWorkItemRelation,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_relation_tools(mcp: FastMCP) -> None:
    """
    Register work-item relation tools on the given MCP instance.
    
    Registers two tools on `mcp`:
    - `create_work_item_relation`: creates one or more relations from a work item to other issues, validating the relation type.
    - `remove_work_item_relation`: removes a relation between a work item and a related issue.
    
    Parameters:
        mcp (FastMCP): MCP server instance to register the tools on.
    """

    @mcp.tool()
    def create_work_item_relation(
        project_id: str,
        work_item_id: str,
        relation_type: str,
        issues: list[str],
    ) -> None:
        """
        Create relations between a work item and other work items in the given project.
        
        Parameters:
            project_id (str): UUID of the project containing the work item.
            work_item_id (str): UUID of the work item to which relations will be added.
            relation_type (str): Relation type; must be one of the allowed WorkItemRelationTypeEnum values.
            issues (list[str]): List of work item IDs to relate to the target work item.
        
        Raises:
            ValueError: If `relation_type` is not one of the allowed WorkItemRelationTypeEnum values.
        """
        client, workspace_slug = get_plane_client_context()

        # Validate relation_type against allowed literal values
        if relation_type not in get_args(WorkItemRelationTypeEnum):
            raise ValueError(
                f"Invalid relation_type '{relation_type}'. " f"Must be one of: {get_args(WorkItemRelationTypeEnum)}"
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
