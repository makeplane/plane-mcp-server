"""Work item relation definition tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_relation_definition_tools(mcp: FastMCP) -> None:
    """Register work item relation definition tools with the MCP server."""

    @mcp.tool()
    def list_work_item_relation_definitions(
        is_default: bool | None = None,
        is_active: bool | None = None,
    ) -> list[WorkItemRelationDefinition]:
        """List all workspace-level relation definitions.

        These definitions describe workspace-level custom relation types. Each
        definition contains an outward and inward label that describe the
        relationship between two work items.

        Use this tool whenever the requested relationship does not exactly match
        one of the six built-in dependency types.

        The returned definitions can be used to identify, validate, or create
        custom relationships between work items.

        All pages are fetched automatically.

        Args:
            is_default: Filter to default or non-default definitions only.
            is_active: Filter to active or inactive definitions only.

        Returns:
            List of WorkItemRelationDefinition objects.
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
            if not page.next_page_results:
                break
            cursor = page.next_cursor
        return results

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
