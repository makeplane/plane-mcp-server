"""Work item relation definition tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)
from plane.models.work_items import DependencyTypeEnum

from plane_mcp.client import get_plane_client_context


def register_work_item_relation_definition_tools(mcp: FastMCP) -> None:
    """Register work item relation definition tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relation_definitions(
        is_default: bool | None = None,
        is_active: bool | None = None,
    ) -> dict[str, Any]:
        """List every relation type usable with create_work_item_relation.

        Match the user's wording against an entry here before creating a relation.
        built_in_dependencies are fixed scheduling/blocking types; custom_definitions
        are workspace-specific, each with an outward and inward label. custom_definitions
        are also what create/update/delete_work_item_relation_definition manage.

        Args:
            is_default: Filter custom definitions to default/non-default only.
            is_active: Filter custom definitions to active/inactive only.

        Returns:
            built_in_dependencies: relation_type values for the dependency path.
            custom_definitions: workspace definitions; use the id plus the matched
                outward or inward label.
        """
        client, workspace_slug = get_plane_client_context()
        results: list[WorkItemRelationDefinition] = []
        cursor: str | None = None
        while True:
            page: PaginatedWorkItemRelationDefinitionResponse = client.work_item_relation_definitions.list(
                workspace_slug=workspace_slug,
                is_default=is_default,
                is_active=is_active,
                per_page=100,
                cursor=cursor,
            )
            results.extend(page.results)
            cursor = page.next_cursor
            if not page.next_page_results or not cursor:
                break
        return {
            "built_in_dependencies": list(get_args(DependencyTypeEnum)),
            "custom_definitions": [d.model_dump() for d in results],
        }

    @mcp.tool()
    def create_work_item_relation_definition(
        name: str,
        outward: str | None = None,
        inward: str | None = None,
        is_active: bool | None = None,
        color: str | None = None,
    ) -> WorkItemRelationDefinition:
        """Create a new workspace relation definition.

        A relation definition describes a named relationship type with an
        outward label (how the source item describes the target) and an
        inward label (how the target item describes the source).

        Args:
            name: Unique name for this relation definition.
            outward: Label describing the relation from the source item's perspective.
            inward: Label describing the relation from the target item's perspective.
            is_active: Whether this definition is active and available for use.
            color: Hex color code for UI display.

        Returns:
            Created WorkItemRelationDefinition object.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateWorkItemRelationDefinition(
            name=name,
            outward=outward,
            inward=inward,
            is_active=is_active,
            color=color,
        )
        return client.work_item_relation_definitions.create(
            workspace_slug=workspace_slug,
            data=data,
        )

    @mcp.tool()
    def update_work_item_relation_definition(
        definition_id: str,
        name: str | None = None,
        outward: str | None = None,
        inward: str | None = None,
        is_active: bool | None = None,
        color: str | None = None,
    ) -> WorkItemRelationDefinition:
        """Update an existing workspace relation definition.

        Args:
            definition_id: UUID of the relation definition to update.
            name: New name for this definition.
            outward: Updated outward label.
            inward: Updated inward label.
            is_active: Updated active status.
            color: Updated hex color code.

        Returns:
            Updated WorkItemRelationDefinition object.
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateWorkItemRelationDefinition(
            name=name,
            outward=outward,
            inward=inward,
            is_active=is_active,
            color=color,
        )
        return client.work_item_relation_definitions.update(
            workspace_slug=workspace_slug,
            definition_id=definition_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_relation_definition(
        definition_id: str,
    ) -> None:
        """Delete a workspace relation definition.

        Args:
            definition_id: UUID of the relation definition to delete.
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_relation_definitions.delete(
            workspace_slug=workspace_slug,
            definition_id=definition_id,
        )
