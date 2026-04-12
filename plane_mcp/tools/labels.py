"""Label-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.labels import (
    CreateLabel,
    Label,
    UpdateLabel,
)

from plane_mcp.client import get_plane_client_context


def register_label_tools(mcp: FastMCP) -> None:
    """Register all label-related tools with the MCP server."""

    @mcp.tool()
    def create_label(
        project_id: str,
        name: str,
        color: str | None = None,
        description: str | None = None,
        parent: str | None = None,
        sort_order: float | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Label:
        """
        Create a new label.

        Args:
            project_id: UUID of the project
            name: Label name
            color: Label color (hex color code)
            description: Label description
            parent: UUID of the parent label (for nested labels)
            sort_order: Sort order for the label
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created Label object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateLabel(
            name=name,
            color=color,
            description=description,
            parent=parent,
            sort_order=sort_order,
            external_source=external_source,
            external_id=external_id,
        )

        return client.labels.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_label(
        project_id: str,
        label_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
        parent: str | None = None,
        sort_order: float | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Label:
        """
        Update an existing label in the specified project.
        
        Parameters:
            project_id: UUID of the project containing the label.
            label_id: UUID of the label to update.
            name: New label name, if provided.
            color: Hex color code for the label, if provided.
            description: New description for the label, if provided.
            parent: UUID of the parent label for nesting, if provided.
            sort_order: Numeric sort order for label positioning, if provided.
            external_source: Name of the external system source, if provided.
            external_id: Identifier from the external system, if provided.
        
        Returns:
            Label representing the updated label.
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateLabel(
            name=name,
            color=color,
            description=description,
            parent=parent,
            sort_order=sort_order,
            external_source=external_source,
            external_id=external_id,
        )

        return client.labels.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            label_id=label_id,
            data=data,
        )

    @mcp.tool()
    def delete_label(project_id: str, label_id: str) -> None:
        """
        Delete a label by ID.

        Args:
            project_id: UUID of the project
            label_id: UUID of the label
        """
        client, workspace_slug = get_plane_client_context()
        client.labels.delete(workspace_slug=workspace_slug, project_id=project_id, label_id=label_id)
