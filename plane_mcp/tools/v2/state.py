"""Workflow states within a project."""

from __future__ import annotations

from typing import Literal, get_args

from fastmcp import FastMCP
from plane.models.enums import GroupEnum
from plane.models.states import CreateState, PaginatedStateResponse, State, UpdateState

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v2._runtime import missing, opt, page_params
from plane_mcp.tools.v2._spec import Action, build_annotations, build_description

NAME = "state"
TITLE = "Workflow states"

GROUPS = get_args(GroupEnum)

ACTIONS = (
    Action("list", ("project_id",), ("cursor", "per_page"), read=True),
    Action("retrieve", ("project_id", "state_id"), read=True),
    Action(
        "create",
        ("project_id", "name", "color"),
        ("description", "sequence", "group", "is_triage", "default", "external_source", "external_id"),
    ),
    Action(
        "update",
        ("project_id", "state_id"),
        ("name", "color", "description", "sequence", "group", "is_triage", "default"),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("project_id", "state_id"), destructive=True),
)

FOOTER = (
    f"group is one of: {', '.join(GROUPS)}. color is a hex code such as #EF4444. "
    "Some workspaces manage states centrally; creating one there returns an error from the API."
)

LEGACY = {
    "list_states": "list",
    "retrieve_state": "retrieve",
    "create_state": "create",
    "update_state": "update",
    "delete_state": "delete",
}


def _group(value: str) -> GroupEnum | None:
    """Accept only a known group; anything else is dropped rather than sent."""
    return value if value in GROUPS else None  # type: ignore[return-value]


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Workflow states within a project.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def state(
        action: Literal["list", "retrieve", "create", "update", "delete"],
        project_id: str = "",
        state_id: str = "",
        name: str = "",
        color: str = "",
        description: str = "",
        # 0 is a real sequence value, so it cannot use the 0 sentinel.
        sequence: float | None = None,
        group: str = "",
        # Tri-state: False is a meaningful value distinct from "not supplied".
        is_triage: bool | None = None,
        default: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> State | list[State] | str | None:
        client, workspace_slug = get_plane_client_context()

        if not project_id:
            return missing(action, "project_id")

        if action == "list":
            response: PaginatedStateResponse = client.states.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=page_params(cursor, per_page),
            )
            return response.results

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
                    group=_group(group),
                    is_triage=is_triage,
                    default=default,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if not state_id:
            return missing(action, "state_id")

        if action == "retrieve":
            return client.states.retrieve(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)

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
                    group=_group(group),
                    is_triage=is_triage,
                    default=default,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        client.states.delete(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
        return None
