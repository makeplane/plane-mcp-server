"""State-related tools for Plane MCP Server."""

from typing import get_args

from fastmcp import FastMCP
from plane.models.enums import GroupEnum
from plane.models.states import (
    CreateState,
    State,
    UpdateState,
)

from plane_mcp.client import get_plane_client_context


def register_state_tools(mcp: FastMCP) -> None:
    """
    Register state-related MCP tools on the provided FastMCP instance.
    """
    """
    Create a new state in the specified project.
    
    Parameters:
        group (str | None): Optional state group; expected values include "backlog", "unstarted", "started", "completed", "cancelled".
        description (str | None): Optional human-readable description.
        sequence (float | None): Optional ordering value for the state.
        is_triage (bool | None): Optional flag indicating a triage state.
        default (bool | None): Optional flag indicating the default state.
        external_source (str | None): Optional external system source name.
        external_id (str | None): Optional external system identifier.
    
    Returns:
        State: The created State object.
    """
    """
    Update an existing state by ID in the specified project.
    
    Parameters:
        group (str | None): Optional state group; expected values include "backlog", "unstarted", "started", "completed", "cancelled".
        description (str | None): Optional human-readable description.
        sequence (float | None): Optional ordering value for the state.
        is_triage (bool | None): Optional flag indicating a triage state.
        default (bool | None): Optional flag indicating the default state.
        external_source (str | None): Optional external system source name.
        external_id (str | None): Optional external system identifier.
    
    Returns:
        State: The updated State object.
    """
    """
    Delete a state by ID from the specified project.
    """

    @mcp.tool()
    def create_state(
        project_id: str,
        name: str,
        color: str,
        description: str | None = None,
        sequence: float | None = None,
        group: str | None = None,
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> State:
        """
        Create a new state in the specified project.
        
        Parameters:
            project_id (str): Project identifier.
            name (str): State name.
            color (str): State color (hex code).
            description (str | None): Optional state description.
            sequence (float | None): Optional ordering position for the state.
            group (str | None): Optional group category; one of "backlog", "unstarted", "started", "completed", "cancelled".
            is_triage (bool | None): Whether the state is a triage state.
            default (bool | None): Whether the state is the project's default.
            external_source (str | None): Optional external system name.
            external_id (str | None): Optional identifier in the external system.
        
        Returns:
            State: The created State object.
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

        return client.states.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def update_state(
        project_id: str,
        state_id: str,
        name: str | None = None,
        color: str | None = None,
        description: str | None = None,
        sequence: float | None = None,
        group: str | None = None,
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> State:
        """
        Update a state by its identifier.
        
        If `group` is provided, it is validated against the allowed GroupEnum literal values; an invalid value will be ignored.
        
        Parameters:
            group (str | None): Optional state group (e.g., "backlog", "unstarted", "started", "completed", "cancelled"). If not one of the allowed literals, the group is not applied.
        
        Returns:
            State: The updated State object.
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
        client.states.delete(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
