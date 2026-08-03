"""Consolidated `work_item_activity` tool.

Collapses work_item_activities.py (2 tools) into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import (
    PaginatedWorkItemActivityResponse,
    WorkItemActivity,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing

ACTIONS = ["list", "retrieve"]

DOC = """Read the activity (change history) of a work item. Actions:
list (project_id, work_item_id; optional params e.g. per_page, cursor);
retrieve (project_id, work_item_id, activity_id)."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    activity_id: str,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")
    if not work_item_id:
        return missing(action, "work_item_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedWorkItemActivityResponse = client.work_items.activities.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return response.results

    if not activity_id:
        return missing(action, "activity_id")
    return client.work_items.activities.retrieve(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        activity_id=activity_id,
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_activity", description=DOC)
    def _work_item_activity(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        activity_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> WorkItemActivity | list[WorkItemActivity] | str | None:
        return _dispatch(action, project_id, work_item_id, activity_id, params)


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_activity", description=DOC)
    def _work_item_activity(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        activity_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(_dispatch(action, project_id, work_item_id, activity_id, params))
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
