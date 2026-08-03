"""Consolidated `project_estimate` tool.

Collapses 9 verb-per-resource tools from plane_mcp/tools/projects.py:
get_project_estimate, create_project_estimate, update_project_estimate,
delete_project_estimate, list_project_estimate_points,
create_project_estimate_points, update_project_estimate_point,
delete_project_estimate_point, link_estimate_to_project.
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.estimates import (
    CreateEstimate,
    CreateEstimatePoint,
    Estimate,
    EstimatePoint,
    UpdateEstimate,
    UpdateEstimatePoint,
)
from plane.models.projects import Project

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "get",
    "create",
    "update",
    "delete",
    "list_points",
    "create_points",
    "update_point",
    "delete_point",
    "link_to_project",
]

DOC = """Manage project estimates and their estimate points. Actions:
get (project_id) -- returns the active estimate incl. its id, needed by the point actions;
create (project_id, name; optional type one of "categories"/"points"/"time", description, last_used default true, external_id, external_source);
update (project_id; plus any of name, description, external_id, external_source);
delete (project_id);
list_points (project_id, estimate_id);
create_points (project_id, estimate_id, points);
update_point (project_id, estimate_id, estimate_point_id; plus any of value, key, description, external_id, external_source);
delete_point (project_id, estimate_id, estimate_point_id);
link_to_project (project_id, estimate_id) -- makes that estimate the active system, returns the Project.

points is a list of dicts; each may have value (required, max 20 chars), key (int sort key),
description, external_id, external_source -- e.g. [{"value": "1", "key": 0}, {"value": "2", "key": 1}].

To set a work item's estimate: get -> list_points -> match EstimatePoint.value ("1"/"2"/"5"
or "XS"/"S"/"M"/"L") -> pass that EstimatePoint.id to update_work_item(estimate_point=...)."""


def _dispatch(
    action: str,
    project_id: str = "",
    estimate_id: str = "",
    estimate_point_id: str = "",
    name: str = "",
    type: str = "",  # noqa: A002 - name kept identical to the source tool
    description: str = "",
    last_used: bool = True,
    external_id: str = "",
    external_source: str = "",
    points: list[dict] | None = None,
    value: str = "",
    key: int | None = None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "get":
        return client.estimates.retrieve(workspace_slug=workspace_slug, project_id=project_id)

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.estimates.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateEstimate(
                name=name,
                type=opt(type),
                description=opt(description),
                last_used=last_used,
                external_id=opt(external_id),
                external_source=opt(external_source),
            ),
        )

    if action == "update":
        return client.estimates.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=UpdateEstimate(
                name=opt(name),
                description=opt(description),
                external_id=opt(external_id),
                external_source=opt(external_source),
            ),
        )

    if action == "delete":
        client.estimates.delete(workspace_slug=workspace_slug, project_id=project_id)
        return None

    if not estimate_id:
        return missing(action, "estimate_id")

    if action == "list_points":
        return client.estimates.list_points(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
        )

    if action == "create_points":
        if not points:
            return missing(action, "points")
        return client.estimates.create_points(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            data=[CreateEstimatePoint(**p) for p in points],
        )

    if action == "link_to_project":
        return client.estimates.link_to_project(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
        )

    if not estimate_point_id:
        return missing(action, "estimate_point_id")

    if action == "update_point":
        return client.estimates.update_point(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            estimate_point_id=estimate_point_id,
            data=UpdateEstimatePoint(
                value=opt(value),
                key=key,
                description=opt(description),
                external_id=opt(external_id),
                external_source=opt(external_source),
            ),
        )

    client.estimates.delete_point(
        workspace_slug=workspace_slug,
        project_id=project_id,
        estimate_id=estimate_id,
        estimate_point_id=estimate_point_id,
    )
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="project_estimate", description=DOC)
    def _project_estimate(
        action: str,
        project_id: str = "",
        estimate_id: str = "",
        estimate_point_id: str = "",
        name: str = "",
        type: str = "",  # noqa: A002 - name kept identical to the source tool
        description: str = "",
        last_used: bool = True,
        external_id: str = "",
        external_source: str = "",
        points: list[dict] | None = None,
        value: str = "",
        key: int | None = None,
    ) -> Estimate | EstimatePoint | list[EstimatePoint] | Project | str | None:
        return _dispatch(
            action,
            project_id=project_id,
            estimate_id=estimate_id,
            estimate_point_id=estimate_point_id,
            name=name,
            type=type,
            description=description,
            last_used=last_used,
            external_id=external_id,
            external_source=external_source,
            points=points,
            value=value,
            key=key,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="project_estimate", description=DOC)
    def _project_estimate(
        action: str,
        project_id: str = "",
        estimate_id: str = "",
        estimate_point_id: str = "",
        name: str = "",
        type: str = "",  # noqa: A002 - name kept identical to the source tool
        description: str = "",
        last_used: bool = True,
        external_id: str = "",
        external_source: str = "",
        points: list[dict] | None = None,
        value: str = "",
        key: int | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action,
                    project_id=project_id,
                    estimate_id=estimate_id,
                    estimate_point_id=estimate_point_id,
                    name=name,
                    type=type,
                    description=description,
                    last_used=last_used,
                    external_id=external_id,
                    external_source=external_source,
                    points=points,
                    value=value,
                    key=key,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            # `type` is shadowed by the parameter above; use __class__ instead.
            return f"Error: {e.__class__.__name__}: {e}"
