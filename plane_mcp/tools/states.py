"""State-related tools for Plane MCP Server."""

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import GroupEnum
from plane.models.states import (
    CreateState,
    PaginatedStateResponse,
    State,
    UpdateState,
)

from plane_mcp.client import get_plane_client_context


def register_state_tools(mcp: FastMCP) -> None:
    """Register all state-related tools with the MCP server."""

    @mcp.tool()
    def list_states(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[State]:
        """
        List all states in a project.

        Args:
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of State objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedStateResponse = client.states.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_state(
        project_id: str,
        name: str,
        color: str,
        description: str | None = None,
        sequence: int | None = None,
        group: str | None = None,
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> State:
        """
        Create a new state.

        Args:
            project_id: UUID of the project
            name: State name
            color: State color (hex color code)
            description: State description
            sequence: State sequence order
            group: State group (e.g., backlog, unstarted, started, completed, cancelled)
            is_triage: Whether this is a triage state
            default: Whether this is the default state
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created State object
        """
        client, workspace_slug = get_plane_client_context()

        # Validate group against allowed literal values
        validated_group: GroupEnum | None = (
            group if group in get_args(GroupEnum) else None  # type: ignore[assignment]
        )

        data = CreateState(
            name=name,
            color=color,
            description=description,
            sequence=sequence,
            group=validated_group,
            is_triage=is_triage,
            default=default,
            external_source=external_source,
            external_id=external_id,
        )

        return client.states.create(
            workspace_slug=workspace_slug, project_id=project_id, data=data
        )

    @mcp.tool()
    def retrieve_state(project_id: str, state_id: str) -> State:
        """
        Retrieve a state by ID.

        Args:
            project_id: UUID of the project
            state_id: UUID of the state

        Returns:
            State object
        """
        client, workspace_slug = get_plane_client_context()
        return client.states.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, state_id=state_id
        )

    @mcp.tool()
    def update_state(
        project_id: str,
        state_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
        sequence: int | None = None,
        group: str | None = None,
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> State:
        """
        Update a state by ID.

        Args:
            project_id: UUID of the project
            state_id: UUID of the state
            name: State name
            color: State color (hex color code)
            description: State description
            sequence: State sequence order
            group: State group (e.g., backlog, unstarted, started, completed, cancelled)
            is_triage: Whether this is a triage state
            default: Whether this is the default state
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated State object
        """
        client, workspace_slug = get_plane_client_context()

        # Validate group against allowed literal values
        validated_group: GroupEnum | None = (
            group if group in get_args(GroupEnum) else None  # type: ignore[assignment]
        )

        data = UpdateState(
            name=name,
            color=color,
            description=description,
            sequence=sequence,
            group=validated_group,
            is_triage=is_triage,
            default=default,
            external_source=external_source,
            external_id=external_id,
        )

        return client.states.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            state_id=state_id,
            data=data,
        )

    @mcp.tool()
    def delete_state(project_id: str, state_id: str) -> None:
        """
        Delete a state by ID.

        Args:
            project_id: UUID of the project
            state_id: UUID of the state
        """
        client, workspace_slug = get_plane_client_context()
        client.states.delete(
            workspace_slug=workspace_slug, project_id=project_id, state_id=state_id
        )
