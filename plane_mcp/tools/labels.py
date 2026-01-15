"""Label-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.labels import (
    CreateLabel,
    Label,
    PaginatedLabelResponse,
    UpdateLabel,
)

from plane_mcp.client import get_plane_client_context


def register_label_tools(mcp: FastMCP) -> None:
    """Register all label-related tools with the MCP server."""

    @mcp.tool()
    def list_labels(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Label]:
        """
        List all labels in a project.

        Args:
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of Label objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedLabelResponse = client.labels.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

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

        return client.labels.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def retrieve_label(project_id: str, label_id: str) -> Label:
        """
        Retrieve a label by ID.

        Args:
            project_id: UUID of the project
            label_id: UUID of the label

        Returns:
            Label object
        """
        client, workspace_slug = get_plane_client_context()
        return client.labels.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, label_id=label_id
        )

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
        Update a label by ID.

        Args:
            project_id: UUID of the project
            label_id: UUID of the label
            name: Label name
            color: Label color (hex color code)
            description: Label description
            parent: UUID of the parent label (for nested labels)
            sort_order: Sort order for the label
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated Label object
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
        client.labels.delete(
            workspace_slug=workspace_slug, project_id=project_id, label_id=label_id
        )
