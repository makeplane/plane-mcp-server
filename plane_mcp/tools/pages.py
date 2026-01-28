"""Page-related tools for Plane MCP Server."""

from typing import Any, Optional

from fastmcp import FastMCP
from plane_mcp.client import get_plane_client_context

from plane.models.pages import CreatePage, Page


def register_page_tools(mcp: FastMCP) -> None:
    """Register all page-related tools with the MCP server."""
    
    @mcp.tool()
    def list_project_pages(
        project_id: str,
        per_page: int = 100,
        cursor: str = "",
    ) -> str:
        """
        List all pages in a project with pagination.
        
        Args:
            project_id: UUID of the project
            per_page: Number of items per page (default: 100)
            cursor: Pagination cursor string
        
        Returns:
            JSON string with pages list
        """
        client, workspace_slug = get_plane_client_context()
        
        # Use the SDK to list project pages
        result = client.projects.pages.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            per_page=per_page,
            cursor=cursor if cursor else None,
        )
        
        # Return as JSON string
        return str(result.model_dump_json())
    
    @mcp.tool()
    def list_workspace_pages(
        per_page: int = 100,
        cursor: str = "",
    ) -> str:
        """
        List all workspace pages with pagination.
        
        Args:
            per_page: Number of items per page (default: 100)
            cursor: Pagination cursor string
        
        Returns:
            JSON string with pages list
        """
        client, workspace_slug = get_plane_client_context()
        
        # Use the SDK to list workspace pages
        result = client.pages.list(
            workspace_slug=workspace_slug,
            per_page=per_page,
            cursor=cursor if cursor else None,
        )
        
        # Return as JSON string
        return str(result.model_dump_json())
    
    @mcp.tool()
    def update_page(
        page_id: str,
        name: str | None = None,
        description_html: str | None = None,
        access: int | None = None,
        color: str | None = None,
        is_locked: bool | None = None,
        archived_at: str | None = None,
        view_props: dict[str, Any] | None = None,
        logo_props: dict[str, Any] | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
        parent_page: str | None = None,
    ) -> str:
        """
        Update an existing page.
        
        Args:
            page_id: UUID of the page
            name: Page name
            description_html: Page content in HTML format
            access: Access level (0=private, 1=public, 2=secret)
            color: Page color
            is_locked: Whether the page is locked
            archived_at: Archive timestamp
            view_props: View properties
            logo_props: Logo properties
            external_id: External ID
            external_source: External source
            parent_page: Parent page ID (for nested pages)
        
        Returns:
            Updated Page object
        """
        client, workspace_slug = get_plane_client_context()
        
        # Build update data with only provided fields
        update_data = {}
        if name is not None:
            update_data["name"] = name
        if description_html is not None:
            update_data["description_html"] = description_html
        if access is not None:
            update_data["access"] = access
        if color is not None:
            update_data["color"] = color
        if is_locked is not None:
            update_data["is_locked"] = is_locked
        if parent_page is not None:
            update_data["parent_page"] = parent_page
        if archived_at is not None:
            update_data["archived_at"] = archived_at
        if view_props is not None:
            update_data["view_props"] = view_props
        if logo_props is not None:
            update_data["logo_props"] = logo_props
        if external_id is not None:
            update_data["external_id"] = external_id
        if external_source is not None:
            update_data["external_source"] = external_source
        
        # Use SDK to update the page
        result = client.pages.update(
            workspace_slug=workspace_slug,
            page_id=page_id,
            **update_data,
        )
        
        return str(result.model_dump_json())
    
    @mcp.tool()
    def delete_page(
        page_id: str,
    ) -> str:
        """
        Delete a page.
        
        Args:
            page_id: UUID of the page
        
        Returns:
            Success message
        """
        client, workspace_slug = get_plane_client_context()
        
        result = client.pages.delete(
            workspace_slug=workspace_slug,
            page_id=page_id,
        )
        
        return str(result.model_dump_json())
    
    @mcp.tool()
    def search_pages(
        query: str,
        workspace_level: bool = False,
    ) -> str:
        """
        Search for pages by query string.
        
        Args:
            query: Search query
            workspace_level: Search workspace pages (True) or project pages (False)
        
        Returns:
            JSON string with search results
        """
        client, workspace_slug = get_plane_client_context()
        
        # Determine search endpoint based on level
        if workspace_level:
            result = client.pages.list(
                workspace_slug=workspace_slug,
                query=query,
            )
        else:
            # For project-level, we'd need project_id
            # This is a limitation - let's return error for now
            return '{"error": "Project page search requires project_id. Use workspace_level=True for workspace-wide search."}'
        
        return str(result.model_dump_json())
