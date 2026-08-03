"""Consolidated `work_item_link` tool.

Collapses the 5 tools in plane_mcp/tools/work_item_links.py into a single
action-dispatch tool. SDK calls and validation are ported verbatim.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import (
    CreateWorkItemLink,
    PaginatedWorkItemLinkResponse,
    UpdateWorkItemLink,
    WorkItemLink,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage external links attached to a work item. Actions:
list (project_id, work_item_id; optional params dict of query parameters);
retrieve (project_id, work_item_id, link_id);
create (project_id, work_item_id, url);
update (project_id, work_item_id, link_id; optional url);
delete (project_id, work_item_id, link_id)."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    link_id: str,
    url: str,
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
        response: PaginatedWorkItemLinkResponse = client.work_items.links.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return response.results

    if action == "create":
        if not url:
            return missing(action, "url")
        return client.work_items.links.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemLink(url=url),
        )

    if not link_id:
        return missing(action, "link_id")

    if action == "retrieve":
        return client.work_items.links.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            link_id=link_id,
        )

    if action == "update":
        return client.work_items.links.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            link_id=link_id,
            data=UpdateWorkItemLink(url=opt(url)),
        )

    client.work_items.links.delete(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        link_id=link_id,
    )
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_link", description=DOC)
    def _work_item_link(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        link_id: str = "",
        url: str = "",
        params: dict[str, Any] | None = None,
    ) -> WorkItemLink | list[WorkItemLink] | str | None:
        return _dispatch(action, project_id, work_item_id, link_id, url, params)


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_link", description=DOC)
    def _work_item_link(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        link_id: str = "",
        url: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(_dispatch(action, project_id, work_item_id, link_id, url, params))
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
