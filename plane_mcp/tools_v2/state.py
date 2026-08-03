"""Consolidated `state` tool.

Collapses the 5 tools in plane_mcp/tools/states.py into one action-dispatch tool.
"""

from __future__ import annotations

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
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage work item states in a project. Actions:
list (project_id; optional params as a query-parameter dict);
retrieve (project_id, state_id);
create (project_id, name, color; optional description, sequence, group, is_triage, default, external_source, external_id);
update (project_id, state_id; only the fields you pass are changed: name, color, description, sequence, group, is_triage, default, external_source, external_id);
delete (project_id, state_id).

color is a hex color code. group is one of: backlog, unstarted, started, completed,
cancelled -- any other value is ignored (sent as unset). sequence is the ordering
position; default marks the project's default state."""


def _dispatch(
    action: str,
    project_id: str,
    state_id: str,
    name: str,
    color: str,
    description: str,
    sequence: float | None,
    group: str,
    is_triage: bool | None,
    default: bool | None,
    external_source: str,
    external_id: str,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedStateResponse = client.states.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    # Validate group against allowed literal values
    validated_group: GroupEnum | None = (
        group if group in get_args(GroupEnum) else None  # type: ignore[assignment]
    )

    if action == "create":
        if not name or not color:
            return missing(action, "name", "color")
        return client.states.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateState(
                name=name,
                color=color,
                description=opt(description),
                sequence=sequence,
                group=validated_group,
                is_triage=is_triage,
                default=default,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not state_id:
        return missing(action, "state_id")

    if action == "retrieve":
        return client.states.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, state_id=state_id
        )

    if action == "update":
        return client.states.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            state_id=state_id,
            data=UpdateState(
                name=opt(name),
                color=opt(color),
                description=opt(description),
                sequence=sequence,
                group=validated_group,
                is_triage=is_triage,
                default=default,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    client.states.delete(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="state", description=DOC)
    def _state(
        action: str,
        project_id: str = "",
        state_id: str = "",
        name: str = "",
        color: str = "",
        description: str = "",
        sequence: float | None = None,
        group: str = "",
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> State | list[State] | str | None:
        return _dispatch(
            action, project_id, state_id, name, color, description, sequence,
            group, is_triage, default, external_source, external_id, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="state", description=DOC)
    def _state(
        action: str,
        project_id: str = "",
        state_id: str = "",
        name: str = "",
        color: str = "",
        description: str = "",
        sequence: float | None = None,
        group: str = "",
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, state_id, name, color, description, sequence,
                    group, is_triage, default, external_source, external_id, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
