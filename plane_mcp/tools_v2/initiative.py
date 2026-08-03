"""Consolidated `initiative` tool -- collapses the 7 verb-per-resource initiative tools.

Mirrors plane_mcp/tools_v2/intake.py. Source of truth: plane_mcp/tools/initiatives.py.

The native-initiatives feature gate (and its per-action fallback guidance) is reused
verbatim from the source module so the error text stays identical.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.initiatives import (
    CreateInitiative,
    Initiative,
    PaginatedInitiativeResponse,
    UpdateInitiative,
)
from plane.models.projects import PaginatedProjectResponse, Project

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.initiatives import _PROJECTS_NEED_NATIVE, _require_native_initiatives
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "list_projects",
    "manage_projects",
]

DOC = """Manage workspace initiatives. Actions:
list (no required params; optional params e.g. per_page, cursor);
retrieve (initiative_id);
create (name; optional description_html, start_date, end_date, logo_props, state, lead);
update (initiative_id; optional name, description_html, start_date, end_date, logo_props, state, lead);
delete (initiative_id);
list_projects (initiative_id; optional params e.g. per_page, cursor);
manage_projects (initiative_id, op, project_ids).

op is "add" to link the projects or "remove" to unlink them; project_ids must not be empty.
manage_projects returns the initiative's linked projects after the operation.
state is one of DRAFT, PLANNED, ACTIVE, COMPLETED, CLOSED. lead is a user UUID. Dates are ISO 8601.
Every action requires the workspace "initiatives" feature. When it is disabled, initiatives are modelled
as "Initiative" work items instead and the error message gives the exact work-item steps to use."""


def _dispatch(
    action: str,
    initiative_id: str,
    name: str,
    description_html: str,
    start_date: str,
    end_date: str,
    state: str,
    lead: str,
    op: str,
    logo_props: dict[str, Any] | None,
    project_ids: list[str] | None,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        _require_native_initiatives(
            client,
            workspace_slug,
            'Initiatives are stored as "Initiative" work items here. List them with '
            'resolve_work_item_type(project_id, "Initiative"), then '
            "list_work_items(project_id, pql='type = \"<type id>\"'). "
            "Work items belong to a project — ask which if not named.",
        )
        response: PaginatedInitiativeResponse = client.initiatives.list(
            workspace_slug=workspace_slug, params=params
        )
        return response.results

    if action == "create":
        if not name:
            return missing(action, "name")
        _require_native_initiatives(
            client,
            workspace_slug,
            f'Create {name!r} as an "Initiative" work item instead:\n'
            "1. Work items belong to a project — if not named, ask the user which project to use.\n"
            '2. type = resolve_work_item_type(project_id, "Initiative") — finds or creates the type automatically.\n'
            f"3. create_work_item(project_id=project_id, type_id=type.id, name={name!r}).",
        )
        return client.initiatives.create(
            workspace_slug=workspace_slug,
            data=CreateInitiative(
                name=name,
                description_html=opt(description_html),
                start_date=opt(start_date),
                end_date=opt(end_date),
                logo_props=logo_props,
                state=opt(state),
                lead=opt(lead),
            ),
        )

    if not initiative_id:
        return missing(action, "initiative_id")

    if action == "retrieve":
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Retrieve it with '
            "retrieve_work_item(project_id, work_item_id) instead.",
        )
        return client.initiatives.retrieve(
            workspace_slug=workspace_slug, initiative_id=initiative_id
        )

    if action == "update":
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Update it with '
            "update_work_item(project_id, work_item_id, ...) instead.",
        )
        return client.initiatives.update(
            workspace_slug=workspace_slug,
            initiative_id=initiative_id,
            data=UpdateInitiative(
                name=opt(name),
                description_html=opt(description_html),
                start_date=opt(start_date),
                end_date=opt(end_date),
                logo_props=logo_props,
                state=opt(state),
                lead=opt(lead),
            ),
        )

    if action == "delete":
        _require_native_initiatives(
            client,
            workspace_slug,
            'This initiative is an "Initiative" work item. Delete it with '
            "delete_work_item(project_id, work_item_id) instead.",
        )
        client.initiatives.delete(workspace_slug=workspace_slug, initiative_id=initiative_id)
        return None

    _require_native_initiatives(client, workspace_slug, _PROJECTS_NEED_NATIVE)

    if action == "list_projects":
        listed: PaginatedProjectResponse = client.initiatives.projects.list(
            workspace_slug=workspace_slug, initiative_id=initiative_id, params=params
        )
        return listed.results

    # action == "manage_projects"
    if op not in ("add", "remove"):
        return missing(action, "op ('add' or 'remove')")
    if not project_ids:
        return missing(action, "project_ids (must not be empty)")
    projects = client.initiatives.projects
    mutate = projects.add if op == "add" else projects.remove
    mutate(workspace_slug=workspace_slug, initiative_id=initiative_id, project_ids=project_ids)

    after: PaginatedProjectResponse = projects.list(
        workspace_slug=workspace_slug, initiative_id=initiative_id
    )
    return after.results


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="initiative", description=DOC)
    def _initiative(
        action: str,
        initiative_id: str = "",
        name: str = "",
        description_html: str = "",
        start_date: str = "",
        end_date: str = "",
        state: str = "",
        lead: str = "",
        op: str = "",
        logo_props: dict[str, Any] | None = None,
        project_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Initiative | list[Initiative] | list[Project] | str | None:
        return _dispatch(
            action, initiative_id, name, description_html, start_date, end_date,
            state, lead, op, logo_props, project_ids, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="initiative", description=DOC)
    def _initiative(
        action: str,
        initiative_id: str = "",
        name: str = "",
        description_html: str = "",
        start_date: str = "",
        end_date: str = "",
        state: str = "",
        lead: str = "",
        op: str = "",
        logo_props: dict[str, Any] | None = None,
        project_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, initiative_id, name, description_html, start_date, end_date,
                    state, lead, op, logo_props, project_ids, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
