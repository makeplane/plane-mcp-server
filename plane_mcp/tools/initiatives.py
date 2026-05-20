"""Initiative-related tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.enums import InitiativeState
from plane.models.initiatives import (
    CreateInitiative,
    Initiative,
    UpdateInitiative,
)

from plane_mcp.client import get_plane_client_context


def register_initiative_tools(mcp: FastMCP) -> None:
    """Register all initiative-related tools with the MCP server."""

    @mcp.tool()
    def create_initiative(
        name: str,
        description_html: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        logo_props: dict | None = None,
        state: InitiativeState | str | None = None,
        lead: str | None = None,
    ) -> Initiative:
        """
        Create a new initiative in the workspace.

        Args:
            workspace_slug: The workspace slug identifier
            name: Initiative name
            description_html: HTML description of the initiative
            start_date: Initiative start date (ISO 8601 format)
            end_date: Initiative end date (ISO 8601 format)
            logo_props: Logo properties dictionary
            state: Initiative state (DRAFT, PLANNED, ACTIVE, COMPLETED, CLOSED)
            lead: UUID of the user who leads the initiative

        Returns:
            Created Initiative object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateInitiative(
            name=name,
            description_html=description_html,
            start_date=start_date,
            end_date=end_date,
            logo_props=logo_props,
            state=state,
            lead=lead,
        )

        return client.initiatives.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def update_initiative(
        initiative_id: str,
        name: str | None = None,
        description_html: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        logo_props: dict | None = None,
        state: InitiativeState | str | None = None,
        lead: str | None = None,
    ) -> Initiative:
        """
        Update an initiative by ID.

        Args:
            workspace_slug: The workspace slug identifier
            initiative_id: UUID of the initiative
            name: Initiative name
            description_html: HTML description of the initiative
            start_date: Initiative start date (ISO 8601 format)
            end_date: Initiative end date (ISO 8601 format)
            logo_props: Logo properties dictionary
            state: Initiative state (DRAFT, PLANNED, ACTIVE, COMPLETED, CLOSED)
            lead: UUID of the user who leads the initiative

        Returns:
            Updated Initiative object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateInitiative(
            name=name,
            description_html=description_html,
            start_date=start_date,
            end_date=end_date,
            logo_props=logo_props,
            state=state,
            lead=lead,
        )

        return client.initiatives.update(workspace_slug=workspace_slug, initiative_id=initiative_id, data=data)

    @mcp.tool()
    def delete_initiative(initiative_id: str) -> None:
        """
        Delete an initiative by ID.

        Args:
            workspace_slug: The workspace slug identifier
            initiative_id: UUID of the initiative
        """
        client, workspace_slug = get_plane_client_context()
        client.initiatives.delete(workspace_slug=workspace_slug, initiative_id=initiative_id)
