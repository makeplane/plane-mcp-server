"""Initiative-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.enums import InitiativeState
from plane.models.initiatives import (
    CreateInitiative,
    Initiative,
    PaginatedInitiativeResponse,
    UpdateInitiative,
)

from plane_mcp.client import get_plane_client_context


def register_initiative_tools(mcp: FastMCP) -> None:
    """Register all initiative-related tools with the MCP server."""

    @mcp.tool()
    def list_initiatives(
        params: dict[str, Any] | None = None,
    ) -> list[Initiative]:
        """
        List all initiatives in a workspace.

        Args:
            params: Optional query parameters as a dictionary (e.g., per_page, cursor)

        Returns:
            List of Initiative objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedInitiativeResponse = client.initiatives.list(workspace_slug=workspace_slug, params=params)
        return response.results

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
            name: Initiative name
            description_html: HTML description of the initiative
            start_date: Initiative start date (ISO 8601 format)
            end_date: Initiative end date (ISO 8601 format)
            logo_props: Logo properties dictionary
            state: Initiative state (DRAFT, PLANNED, ACTIVE, COMPLETED, CLOSED)
            lead: UUID of the user who leads the initiative

        Returns:
            Created Initiative object

        Raises:
            ToolError: if the workspace's initiatives feature is disabled. Native
                initiatives require the feature to be enabled in workspace settings.
                When disabled, create an "Initiative" work item instead
                (see the "Initiatives" server instructions).
        """
        client, workspace_slug = get_plane_client_context()

        features = client.workspaces.get_features(workspace_slug=workspace_slug)
        if not features.model_dump().get("initiatives"):
            raise ToolError(
                f"The initiatives feature is disabled for this workspace. "
                f"Create {repr(name)} as an \"Initiative\" work item instead:\n"
                f"1. Work items belong to a project — if not named, ask the user which project to use.\n"
                f"2. type = resolve_work_item_type(project_id, \"Initiative\") — finds or creates the type at workspace or project level automatically.\n"
                f"3. create_work_item(project_id=project_id, type_id=type.id, name={repr(name)})."
            )

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
    def retrieve_initiative(initiative_id: str) -> Initiative:
        """
        Retrieve an initiative by ID.

        Args:
            initiative_id: UUID of the initiative

        Returns:
            Initiative object
        """
        client, workspace_slug = get_plane_client_context()
        return client.initiatives.retrieve(workspace_slug=workspace_slug, initiative_id=initiative_id)

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
            initiative_id: UUID of the initiative
        """
        client, workspace_slug = get_plane_client_context()
        client.initiatives.delete(workspace_slug=workspace_slug, initiative_id=initiative_id)
