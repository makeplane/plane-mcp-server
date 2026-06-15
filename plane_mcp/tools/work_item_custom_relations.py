"""Work item custom relation tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    WorkItemWithRelationType,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_custom_relation_tools(mcp: FastMCP) -> None:
    """Register work item custom relation tools with the MCP server."""

    @mcp.tool()
    def list_work_item_custom_relations(
        project_id: str,
        work_item_id: str,
    ) -> dict[str, list[WorkItemWithRelationType]]:
        """List custom (definition-based) relations for a work item.

        Returns a dict keyed by the outward and inward labels of all active
        workspace relation definitions. Each key maps to a list of work item
        objects that hold that relation with this item.

        Use list_work_item_relation_definitions to discover available definitions
        and their labels.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the work item.

        Returns:
            Dict mapping relation label to list of WorkItemWithRelationType objects.
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.custom_relations.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )

    @mcp.tool()
    def create_work_item_custom_relation(
        project_id: str,
        work_item_id: str,
        relation_definition_id: str,
        relation_definition_type: str,
        work_item_ids: list[str],
    ) -> list[WorkItemWithRelationType]:
        """Create one or more custom relations for a work item.

        Uses a workspace relation definition to establish the relation type.
        relation_definition_type must match either the outward or inward label
        of the specified definition — this controls directionality.

        Use list_work_item_relation_definitions to get definition IDs and their labels.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            relation_definition_id: UUID of the workspace relation definition.
            relation_definition_type: The outward or inward label of the definition.
            work_item_ids: UUIDs of target work items to create relations with.

        Returns:
            List of created WorkItemWithRelationType objects.
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.custom_relations.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemCustomRelation(
                relation_definition_id=relation_definition_id,
                relation_definition_type=relation_definition_type,
                work_item_ids=work_item_ids,
            ),
        )

    @mcp.tool()
    def remove_work_item_custom_relation(
        project_id: str,
        work_item_id: str,
        related_work_item_id: str,
    ) -> None:
        """Remove a custom relation between two work items.

        Removes any custom (definition-based) relation between the source and
        target work item, regardless of direction.

        Args:
            project_id: UUID of the project.
            work_item_id: UUID of the source work item.
            related_work_item_id: UUID of the related work item to remove the relation with.
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.custom_relations.remove(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            related_work_item_id=related_work_item_id,
        )
