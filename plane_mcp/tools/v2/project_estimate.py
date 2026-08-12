"""A project's estimate system and its points.

A project has at most one estimate. To set a work item's estimate: retrieve the
estimate, list its points, then pass the chosen point's id to work_item update
as estimate_point.
"""

from __future__ import annotations

import json
from typing import Literal

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
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, opt

NAME = "project_estimate"
TITLE = "Project estimates"

TYPES = ("categories", "points", "time")

ACTIONS = (
    Action("retrieve", ("project_id",), note="a project has at most one estimate", read=True),
    Action("create", ("project_id", "name"), ("type", "description", "last_used", "external_source", "external_id")),
    Action("update", ("project_id",), ("name", "description", "external_source", "external_id")),
    Action("delete", ("project_id",), destructive=True),
    Action("link", ("project_id", "estimate_id"), note="makes that estimate the project's active one"),
    Action("list_points", ("project_id", "estimate_id"), read=True),
    Action("create_points", ("project_id", "estimate_id", "points")),
    Action(
        "update_point",
        ("project_id", "estimate_id", "estimate_point_id"),
        ("value", "key", "description", "external_source", "external_id"),
    ),
    Action("delete_point", ("project_id", "estimate_id", "estimate_point_id"), destructive=True),
)

FOOTER = (
    f'type is one of: {", ".join(TYPES)}. A point\'s `value` is its display label ("5", "XL") and '
    'its `key` is the sort order. points takes a JSON array such as [{"value": "1", "key": 0}]. '
    "To set a work item's estimate: retrieve to get the estimate_id, list_points to see the "
    "available values, then pass the chosen point's id to `work_item update` as estimate_point."
)

LEGACY = {
    "get_project_estimate": "retrieve",
    "create_project_estimate": "create",
    "update_project_estimate": "update",
    "delete_project_estimate": "delete",
    "link_estimate_to_project": "link",
    "list_project_estimate_points": "list_points",
    "create_project_estimate_points": "create_points",
    "update_project_estimate_point": "update_point",
    "delete_project_estimate_point": "delete_point",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("A project's estimate system and its points.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def project_estimate(
        action: Literal[
            "retrieve",
            "create",
            "update",
            "delete",
            "link",
            "list_points",
            "create_points",
            "update_point",
            "delete_point",
        ],
        project_id: str = "",
        estimate_id: str = "",
        estimate_point_id: str = "",
        name: str = "",
        type: str = "",
        description: str = "",
        points: str = "",
        value: str = "",
        # Estimate point keys start at 0, so key cannot use the 0 sentinel.
        key: int | None = None,
        last_used: bool = True,
        external_source: str = "",
        external_id: str = "",
    ) -> Estimate | EstimatePoint | list[EstimatePoint] | Project | str | None:
        client, workspace_slug = get_plane_client_context()

        if not project_id:
            return missing(action, "project_id")
        if type and type not in TYPES:
            return f"Error: type must be one of: {', '.join(TYPES)}."

        estimates = client.estimates

        if action == "retrieve":
            return estimates.retrieve(workspace_slug=workspace_slug, project_id=project_id)

        if action == "create":
            if not name:
                return missing(action, "name")
            return estimates.create(
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
            return estimates.update(
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
            estimates.delete(workspace_slug=workspace_slug, project_id=project_id)
            return None

        if not estimate_id:
            return missing(action, "estimate_id")

        if action == "link":
            return estimates.link_to_project(
                workspace_slug=workspace_slug, project_id=project_id, estimate_id=estimate_id
            )

        if action == "list_points":
            return estimates.list_points(workspace_slug=workspace_slug, project_id=project_id, estimate_id=estimate_id)

        if action == "create_points":
            try:
                parsed = json.loads(points) if points else None
            except ValueError:
                return 'Error: points must be a JSON array, for example [{"value": "1", "key": 0}].'
            if not isinstance(parsed, list) or not parsed:
                return missing(action, "points")
            return estimates.create_points(
                workspace_slug=workspace_slug,
                project_id=project_id,
                estimate_id=estimate_id,
                data=[CreateEstimatePoint(**point) for point in parsed],
            )

        if not estimate_point_id:
            return missing(action, "estimate_point_id")

        if action == "update_point":
            return estimates.update_point(
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

        estimates.delete_point(
            workspace_slug=workspace_slug,
            project_id=project_id,
            estimate_id=estimate_id,
            estimate_point_id=estimate_point_id,
        )
        return None
