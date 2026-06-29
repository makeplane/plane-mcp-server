"""Role-related tools for Plane MCP Server."""

from typing import Literal

from fastmcp import FastMCP
from plane.models.roles import PaginatedRoleResponse, Role

from plane_mcp.client import get_plane_client_context


def register_role_tools(mcp: FastMCP) -> None:
    """Register all role-related tools with the MCP server."""

    @mcp.tool()
    def list_roles(
        namespace: Literal["workspace", "project"] | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
    ) -> PaginatedRoleResponse:
        """
        List role definitions in the workspace.

        namespace="workspace" → Owner/Admin/Member/Guest; "project" → project-role
        defs shared across all projects (Admin/Contributor/Commenter/Guest); omit
        for both. slug is stable but NOT globally unique (admin/guest exist in
        both namespaces) — key by (namespace, slug).

        Args:
            namespace: "workspace" or "project"; omit for both.
            per_page: Results per page (server default 20).
            cursor: Prior response's next_cursor.

        Returns:
            Paginated envelope: results (name, slug, namespace) + pagination fields.
        """
        client, workspace_slug = get_plane_client_context()
        return client.roles.list(
            workspace_slug=workspace_slug,
            namespace=namespace,
            per_page=per_page,
            cursor=cursor,
        )

    @mcp.tool()
    def retrieve_role(role_id: str) -> Role:
        """
        Retrieve a role definition by ID.

        Args:
            role_id: UUID of the role.

        Returns:
            Role (name, slug, namespace).
        """
        client, workspace_slug = get_plane_client_context()
        return client.roles.retrieve(workspace_slug=workspace_slug, role_id=role_id)
