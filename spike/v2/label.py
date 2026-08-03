"""Consolidated `label` tool.

Collapses the 5 tools in plane_mcp/tools/labels.py into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.labels import (
    CreateLabel,
    Label,
    PaginatedLabelResponse,
    UpdateLabel,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage work item labels in a project. Actions:
list (project_id; optional params as a query-parameter dict);
retrieve (project_id, label_id);
create (project_id, name; optional color, description, parent, sort_order, external_source, external_id);
update (project_id, label_id; only the fields you pass are changed: name, color, description, parent, sort_order, external_source, external_id);
delete (project_id, label_id).

color is a hex color code. parent is the UUID of a parent label (for nested labels)."""


def _dispatch(
    action: str,
    project_id: str,
    label_id: str,
    name: str,
    color: str,
    description: str,
    parent: str,
    sort_order: float | None,
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
        response: PaginatedLabelResponse = client.labels.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.labels.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateLabel(
                name=name,
                color=opt(color),
                description=opt(description),
                parent=opt(parent),
                sort_order=sort_order,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not label_id:
        return missing(action, "label_id")

    if action == "retrieve":
        return client.labels.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, label_id=label_id
        )

    if action == "update":
        return client.labels.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            label_id=label_id,
            data=UpdateLabel(
                name=opt(name),
                color=opt(color),
                description=opt(description),
                parent=opt(parent),
                sort_order=sort_order,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    client.labels.delete(workspace_slug=workspace_slug, project_id=project_id, label_id=label_id)
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="label", description=DOC)
    def _label(
        action: str,
        project_id: str = "",
        label_id: str = "",
        name: str = "",
        color: str = "",
        description: str = "",
        parent: str = "",
        sort_order: float | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> Label | list[Label] | str | None:
        return _dispatch(
            action, project_id, label_id, name, color, description, parent,
            sort_order, external_source, external_id, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="label", description=DOC)
    def _label(
        action: str,
        project_id: str = "",
        label_id: str = "",
        name: str = "",
        color: str = "",
        description: str = "",
        parent: str = "",
        sort_order: float | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, label_id, name, color, description, parent,
                    sort_order, external_source, external_id, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
