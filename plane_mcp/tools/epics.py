"""Epic-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.epics import Epic, PaginatedEpicResponse
from plane.models.query_params import PaginatedQueryParams, RetrieveQueryParams

from plane_mcp.client import get_plane_client_context


def register_epic_tools(mcp: FastMCP) -> None:
    """Register all epic-related tools with the MCP server."""

    @mcp.tool()
    def list_epics(
        project_id: str,
        cursor: str | None = None,
        per_page: int | None = None,
        expand: str | None = None,
        fields: str | None = None,
        order_by: str | None = None,
    ) -> list[Epic]:
        """
        List all epics in a project.

        Args:
            project_id: UUID of the project
            cursor: Pagination cursor for getting next set of results
            per_page: Number of results per page (1-100)
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response
            order_by: Field to order results by. Prefix with '-' for descending order

        Returns:
            List of Epic objects
        """
        client, workspace_slug = get_plane_client_context()

        params = PaginatedQueryParams(
            cursor=cursor,
            per_page=per_page,
            expand=expand,
            fields=fields,
            order_by=order_by,
        )

        response: PaginatedEpicResponse = client.epics.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            params=params,
        )

        return response.results

    @mcp.tool()
    def retrieve_epic(
        project_id: str,
        epic_id: str,
        expand: str | None = None,
        fields: str | None = None,
    ) -> Epic:
        """
        Retrieve an epic by ID.

        Args:
            project_id: UUID of the project
            epic_id: UUID of the epic
            expand: Comma-separated list of related fields to expand in response
            fields: Comma-separated list of fields to include in response

        Returns:
            Epic object
        """
        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(
            expand=expand,
            fields=fields,
        )

        return client.epics.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            epic_id=epic_id,
            params=params,
        )
