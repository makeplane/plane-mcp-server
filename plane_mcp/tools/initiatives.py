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
    """
    Register initiative-related MCP tools (create, update, delete) on the provided FastMCP instance.
    """
    """
    Create a new initiative in the current workspace.
    
    Parameters:
        name (str): Initiative name.
        description_html (str | None): HTML description of the initiative.
        start_date (str | None): ISO 8601 start date.
        end_date (str | None): ISO 8601 end date.
        logo_props (dict | None): Logo properties.
        state (InitiativeState | str | None): Initiative state (e.g., "DRAFT", "PLANNED", "ACTIVE", "COMPLETED", "CLOSED").
        lead (str | None): UUID of the user who leads the initiative.
    
    Returns:
        Initiative: The created initiative object.
    """
    """
    Update an existing initiative by ID in the current workspace.
    
    Parameters:
        initiative_id (str): UUID of the initiative to update.
        name (str | None): New initiative name.
        description_html (str | None): New HTML description.
        start_date (str | None): New ISO 8601 start date.
        end_date (str | None): New ISO 8601 end date.
        logo_props (dict | None): New logo properties.
        state (InitiativeState | str | None): New initiative state (e.g., "DRAFT", "PLANNED", "ACTIVE", "COMPLETED", "CLOSED").
        lead (str | None): UUID of the user who leads the initiative.
    
    Returns:
        Initiative: The updated initiative object.
    """
    """
    Delete an initiative by ID from the current workspace.
    
    Parameters:
        initiative_id (str): UUID of the initiative to delete.
    """

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
        Create a new initiative in the current Plane workspace.
        
        Parameters:
            name (str): Initiative name.
            description_html (str | None): Optional HTML description.
            start_date (str | None): Optional start date in ISO 8601 format.
            end_date (str | None): Optional end date in ISO 8601 format.
            logo_props (dict | None): Optional dictionary of logo properties.
            state (InitiativeState | str | None): Optional initiative state, e.g. "DRAFT", "PLANNED", "ACTIVE", "COMPLETED", or "CLOSED".
            lead (str | None): Optional UUID string of the user who leads the initiative.
        
        Returns:
            Initiative: The created Initiative object.
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
        Update an existing initiative's fields by its ID.
        
        Parameters:
            initiative_id (str): UUID of the initiative to update.
            name (str | None): New initiative name.
            description_html (str | None): HTML description content.
            start_date (str | None): Start date in ISO 8601 format (YYYY-MM-DD or full timestamp).
            end_date (str | None): End date in ISO 8601 format (YYYY-MM-DD or full timestamp).
            logo_props (dict | None): Dictionary of logo properties (e.g., URL, alt text).
            state (InitiativeState | str | None): Initiative state (e.g., "DRAFT", "PLANNED", "ACTIVE", "COMPLETED", "CLOSED").
            lead (str | None): UUID of the user who will be the initiative lead.
        
        Returns:
            Initiative: The updated Initiative object.
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
