"""Consolidated `work_log` tool.

Collapses work_logs.py (4 tools) into one action-dispatch tool. The source module
has no retrieve tool, so neither does this one.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import WorkItemWorkLog

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing

ACTIONS = ["list", "create", "update", "delete"]

DOC = """Manage the work logs (time tracked) on a work item. Actions:
list (project_id, work_item_id; optional params e.g. per_page, cursor);
create (project_id, work_item_id; optional duration, description);
update (project_id, work_item_id, work_log_id; optional duration, description);
delete (project_id, work_item_id, work_log_id).

duration is the work time in minutes. description is the work performed.
On update only the fields you pass are changed.
There is no retrieve action -- use list and pick the entry you want."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    work_log_id: str,
    duration: int,
    description: str,
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
        return client.work_items.work_logs.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )

    data: dict[str, Any] = {}
    if duration:
        data["duration"] = duration
    if description:
        data["description"] = description

    if action == "create":
        return client.work_items.work_logs.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=data,
        )

    if not work_log_id:
        return missing(action, "work_log_id")

    if action == "delete":
        client.work_items.work_logs.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            work_log_id=work_log_id,
        )
        return None

    return client.work_items.work_logs.update(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        work_log_id=work_log_id,
        data=data,
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_log", description=DOC)
    def _work_log(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_log_id: str = "",
        duration: int = 0,
        description: str = "",
        params: dict[str, Any] | None = None,
    ) -> WorkItemWorkLog | list[WorkItemWorkLog] | str | None:
        return _dispatch(
            action, project_id, work_item_id, work_log_id, duration, description, params
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_log", description=DOC)
    def _work_log(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_log_id: str = "",
        duration: int = 0,
        description: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, work_log_id,
                    duration, description, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
