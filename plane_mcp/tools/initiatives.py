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


def _require_native_initiatives(client: Any, workspace_slug: str, fallback: str) -> None:
    """Raise a ToolError with fallback guidance when the initiatives feature is off.

    Native initiative endpoints only work when the workspace "initiatives" feature
    is enabled. When it is off, initiatives are modeled as "Initiative" work items,
    so every native initiative tool must redirect the caller to the work-item path.
    """
    features = client.workspaces.get_features(workspace_slug=workspace_slug)
    if not features.model_dump().get("initiatives"):
        raise ToolError(f"The initiatives feature is disabled for this workspace. {fallback}")


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

        Raises:
            ToolError: if the initiatives feature is disabled. When disabled,
                initiatives are "Initiative" work items — the error gives the steps.
        """
        client, workspace_slug = get_plane_client_context()
        _require_native_initiatives(
            client,
            workspace_slug,
            'Initiatives are stored as "Initiative" work items here. List them with '
            'resolve_work_item_type(project_id, "Initiative"), then '
            "list_work_items(project_id, pql='type = \"<type id>\"'). "
            "Work items belong to a project — ask which if not named.",
        )
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
                When disabled, create an "Initiative" work item instead — the error
                message gives the exact steps.
        """
        client, workspace_slug = get_plane_client_context()

        _require_native_initiatives(
            client,
            workspace_slug,
            f'Create {name!r} as an "Initiative" work item instead:\n'
            "1. Work items belong to a project — if not named, ask the user which project to use.\n"
            '2. type = resolve_work_item_type(project_id, "Initiative") — finds or creates the type automatically.\n'
            f"3. create_work_item(project_id=project_id, type_id=type.id, name={name!r}).",
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

        Raises:
            ToolError: if the initiatives feature is disabled. When disabled, the
                initiative is an "Initiative" work item — the error gives the steps.
        """
        client, workspace_slug = get_plane_client_context()
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Retrieve it with '
            "retrieve_work_item(project_id, work_item_id) instead.",
        )
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

        Raises:
            ToolError: if the initiatives feature is disabled. When disabled, the
                initiative is an "Initiative" work item — the error gives the steps.
        """
        client, workspace_slug = get_plane_client_context()
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Update it with '
            "update_work_item(project_id, work_item_id, ...) instead.",
        )

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

        Raises:
            ToolError: if the initiatives feature is disabled. When disabled, the
                initiative is an "Initiative" work item — the error gives the steps.
        """
        client, workspace_slug = get_plane_client_context()
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Delete it with '
            "delete_work_item(project_id, work_item_id) instead.",
        )
        client.initiatives.delete(workspace_slug=workspace_slug, initiative_id=initiative_id)
